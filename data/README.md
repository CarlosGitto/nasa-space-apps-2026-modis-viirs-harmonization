# data/

Local data shared by the repo's services. Written by the ETL (`etl/`), read by
anyone else (backend, notebooks...). Not committed to git, except this README.

## Layout: a DuckLake

```
data/lake/
├── catalog.sqlite (+ -wal, -shm)   # DuckLake catalog: which files make up each table and version
└── files/                          # Parquet files managed by DuckLake (don't edit by hand)
```

Everything is organized around **the hexagon**: the H3 resolution-6 cell (~36 km²)
each detection falls in (`h3_r6`), the project's fixed, sensor-agnostic spatial unit.

| Layer | Tables | What |
|---|---|---|
| bronze `firms_raw` | `modis_sp`, `viirs_noaa20_nrt`, … (one per FIRMS product) | Raw detections from dlt, append-only: every load kept (`_dlt_load_id`) |
| silver | `stg_firms__<table>` (views) | Cleaning, types, `dq_issue`, origin columns (sensor, satellite, product), the hexagon |
| silver | `int_detections_sp`, `int_detections_nrt` | One table per product: satellites unioned, latest version per detection |
| silver | `int_static_sources` | Mask of static heat sources (refineries, gas flares, volcanoes) built from SP |
| **gold** | `dim_hexagon` | One row per hexagon: center, real area, parents, activity, `has_static_source` |
| **gold** | `fct_hexagon_detections` | Detail: one row per valid detection, SP and NRT, with `is_preferred` and `is_static_source` |
| **gold** | `fct_hexagon_daily` | Metrics: hexagon × day × product × sensor × satellite × day/night |
| **gold** | `fct_hexagon_monthly` | Continuous series (`is_preferred`) per hexagon × month × sensor × satellite |
| **gold** | `fct_hexagon_profile` | The hexagon's "ficha": one row per hexagon × sensor with its whole history (last detection, last 7/30 days, totals, FRP max/mean/p90, peak day and month, 12-month climatology, static share) |
| bronze `weather_raw` | `merra2_slv_hourly`, `imerg_final_daily`, `imerg_late_daily` | NASA Earthdata weather grids from dlt, values as in the file, append-only |
| silver | `stg_weather__<table>` (views) | Units (°C, m/s, mm), relative humidity, VPD, wind speed and direction, `dq_issue` |
| silver | `int_weather_merra2_hourly`, `int_weather_imerg_daily` | Latest version per grid cell and time step; IMERG Final preferred over Late |
| **gold** | `fct_weather_daily` | MERRA-2 cell × UTC day: temperature, dew point, relative humidity, VPD, wind (max, mean, direction) |
| **gold** | `fct_precipitation_daily` | IMERG cell × UTC day: precipitation (mm), `imerg_run` final or late |

How to read gold:

- **`is_preferred`**: filter on it for one continuous series, SP where NASA has
  published it and NRT for the days after (per satellite), without duplicates.
- **`is_static_source`**: heat that is not a wildfire. SP flags it (`type` 1/2/3);
  NRT is matched against `int_static_sources`. The metrics count it apart
  (`static_detections`), never as fire.
- **Never add satellites or sensors together** as if they were the same: each one
  sees fire differently. The metrics keep them on separate rows.
- **No row means "no detection", not "no fire"**: there is no cloud/coverage mask yet.

Writes are atomic (a reader never sees a half-written table) and every change
is a snapshot, kept 7 days for time travel (`firms_lake_maintenance` DAG).

## Reading it

With DuckDB (1.5.6, same as the ETL), from the repo root:

```sql
INSTALL ducklake; INSTALL sqlite;
-- The catalog stores the data path the ETL used inside its containers;
-- OVERRIDE_DATA_PATH points it to the same files as seen from here.
ATTACH 'ducklake:sqlite:data/lake/catalog.sqlite' AS lake
    (READ_ONLY, DATA_PATH 'data/lake/files/', OVERRIDE_DATA_PATH true);

-- Continuous VIIRS series (SP + NRT), wildfires only
SELECT * FROM lake.gold.fct_hexagon_detections
WHERE sensor = 'viirs' AND is_preferred AND NOT is_static_source
LIMIT 10;

-- Time travel
SELECT * FROM lake.snapshots();
SELECT count(*) FROM lake.gold.fct_hexagon_daily AT (VERSION => 42);
```

Always attach `READ_ONLY` unless you are the ETL: the SQLite catalog takes a
single writer at a time.

## Local dev dataset

For development we work on a fixed slice, so everyone has the same data:
**South America, all four satellites (MODIS, VIIRS S-NPP, NOAA-20, NOAA-21),
continuous from 2025-01-01 to today**:

```
SP  (science quality):  2025-01-01 ──────────────▶ last published month (VIIRS ~3 months behind, MODIS ~2)
NRT (near real time):                   day after SP ──────────────▶ yesterday
```

1. In `etl/.env` set `FIRMS_AREA=-82,-56,-34,13` (see `etl/.env.example`), so the
   scheduled NRT/SP runs keep the same South America coverage.
2. `make etl-start`, then `make etl-seed-dev`: quarterly SP loads plus one NRT
   backfill, run one after the other (~2 h in total; follow them in the Airflow UI).
3. After that the scheduled runs keep it current on their own: NRT daily (last
   2 days) and SP weekly (each new month FIRMS publishes).

Instead of downloading, you can copy someone's `data/lake/` folder (stop Airflow
first so nothing is writing): the catalog and its files travel together.

## Weather (NASA Earthdata)

Weather comes from two NASA products, loaded by the `weather_ingest` DAG and
transformed by `weather_dbt`:

| Product | Variables | Grid | Published |
|---|---|---|---|
| MERRA-2 `M2T1NXSLV` (hourly reanalysis) | 2 m temperature and dew point, 10 m wind | 0.5° × 0.625° (~55 km) | ~3–6 weeks late |
| GPM IMERG daily, Final / Late | precipitation | 0.1° (~10 km) | Late ~14 h; Final months later |

How to read it:

- **Keyed by grid cell, not by hexagon.** One MERRA-2 cell covers ~90 hexagons;
  an IMERG cell, ~3. To get a hexagon's weather, take the cell whose center
  (`cell_lat`, `cell_lon`) is nearest to the hexagon's center (the backend's
  `/hexagons/{h3}/weather` does exactly that).
- **UTC days** (`obs_date`), like FIRMS `acq_date`, so they join on the same date.
- **Relative humidity and VPD** are derived from temperature and dew point
  (Magnus formula); wind direction is where the wind comes from (0° = north).
- **The most recent weeks have no MERRA-2 yet**: accepted for now. Precipitation
  is there (IMERG Late) and switches to Final when NASA publishes it.
- **Accumulations** (last 7 / 30 days, days since rain) are not stored: the
  backend computes them for the requested cell and dates.

Loading the dev weather: `make etl-seed-weather-dev` (Airflow running, after
adding the `earthdata_default` connection to `etl/.env`, see `etl/.env.example`).

**Weather follows the fires** (`WEATHER_KEEP=fires`, the default). A global grid
every day would be billions of rows, and the map only shows weather around fires,
so each month of weather keeps only the grid cells within ~100–150 km of that
month's fire hexagons (their H3 res-3 parents plus one ring;
`etl/include/weather/fires.py`). That decides *what* to download; *when*:

- Fire first. The weather DAG only plans days whose fires, loaded with the same
  area (`_firms_area` in gold), are already in gold, walking forward from where
  each weather product stopped and halting at the first gap of more than 7 days.
- Each product keeps its own range: MERRA-2 up to its last published day
  (~3–6 weeks behind), IMERG Final up to its own (months behind), IMERG Late
  after Final. A month never runs with only part of its fires; it's picked up by
  the next daily run once FIRMS has them.
- Fires that land later for a month whose weather is already loaded (e.g. late
  NRT detections in a new place) don't get their weather automatically: re-run
  `weather_ingest` with `start_date`/`end_date` for that month.

**Ingest runs load everything missing**, in chunks: `firms_ingest_sp` and
`weather_ingest` plan every day from where they stopped (or 2025-01-01) to what
NASA has published and load it one month per task (dynamic task mapping), so a
long backfill lands month by month and a failure retries only its month.
`firms_ingest_nrt` also catches up the days it missed if Airflow was off.

## Changing the region or the dates

Fire and weather cover **the same area and dates on purpose**: weather is only
useful where and when there are detections to explain. If you change one,
change the other:

| What | Where |
|---|---|
| Area of the scheduled runs | `FIRMS_AREA` in `etl/.env` (weather follows it; `WEATHER_AREA` only to override) |
| Area of the dev seeds | `DEV_AREA` in the Makefile (used by `etl-seed-dev` and `etl-seed-weather-dev`) |
| Dates of the dev seeds | `DEV_START` / `DEV_NRT_START` in the Makefile |
| First day for an empty lake | `SP_DEFAULT_START` (`etl/dags/firms_ingest_sp.py`) and `WEATHER_DEFAULT_START` (`etl/include/weather/config.py`) |

Data already in the lake for the old area stays there: changing the area only
affects new loads. To go from South America to **the whole world**:
`FIRMS_AREA=world` in `etl/.env`, `make etl-restart`, then
`make etl-seed-dev DEV_AREA=world` and `make etl-seed-weather-dev DEV_AREA=world`
(hours of downloads, ~25–35 GB of fire data plus a few GB of weather near the
fires; the weather follows as the world's fires land).

## Setup (once per machine)

`make etl-setup` does it. Airflow runs as uid 50000 inside the containers and
writes here through a bind mount (`etl/docker-compose.override.yml`). Let it
write, and keep your own user able to read/delete what it creates:

```bash
mkdir -p data
chmod 777 data
setfacl -m u:$(id -u):rwx -d -m u:$(id -u):rwx data
```

**macOS:** the catalog runs in SQLite WAL mode so readers never block the ETL.
WAL shares memory between processes, which works between a Linux host and
Docker but not across Docker Desktop's VM on macOS: there, run the backend in
a container too instead of on the host.
