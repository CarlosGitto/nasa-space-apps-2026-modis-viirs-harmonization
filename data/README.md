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
| bronze `weather_raw` | `power_daily` | NASA POWER daily weather near fires, from dlt, append-only |
| silver | `stg_weather__power_daily` (view) | Types, `dq_issue` |
| silver | `int_weather_power_daily` | Latest version per grid cell and day |
| **gold** | `fct_weather_daily` | Grid cell (MERRA-2 grid) × UTC day near fires: max temperature, precipitation, mean wind (speed, direction). Feeds the map's weather layers |

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

## Weather (NASA POWER)

Weather comes from [NASA POWER](https://power.larc.nasa.gov): MERRA-2 meteorology
(GEOS-IT for the latest days) already aggregated per UTC day, on the MERRA-2 grid
(0.5° × 0.625°, ~55 km), from 1981 to ~2 days ago, with no login. Two uses:

- **The hexagon panel** (Clima tab, Ficha · período) asks POWER on demand for the
  hexagon's center, through the backend (`/hexagons/{h3}/weather`): any place on
  Earth, nothing to ingest. Answers are cached per POWER cell and day in
  `data/cache/power.sqlite` (recent days refreshed at most every 6 hours), so a
  place is asked once: POWER warns that repeatedly requesting the same location
  can get a client blocked.
- **The map's weather layers** need many cells per day at once, so they read the
  lake: the `weather_ingest` DAG loads POWER's daily regional values (max
  temperature, precipitation, mean wind speed and direction) near fires only,
  `weather_dbt` turns them into `gold.fct_weather_daily`.

How to read the lake's weather:

- **Keyed by grid cell, not by hexagon** (`cell_lat`, `cell_lon`): one cell covers
  ~90 hexagons; take the cell under a point.
- **UTC days** (`obs_date`), like FIRMS `acq_date`, so they join on the same date.
- Wind direction is where the wind comes from (0° = north).

POWER's limits (https://power.larc.nasa.gov/docs/services/api/temporal/daily/): no
published rate, HTTP 429 when hit too hard, one parameter per regional request,
boxes of at most 10° a side (the API rejects more). The ETL asks one request at a
time, a second apart, backing off on 429; it never asks the same box, parameter and
days twice (it resumes after the last day loaded).

Loading the dev weather: `make etl-seed-weather-dev` (Airflow running; no credentials).

**Weather follows the fires** (`WEATHER_KEEP=fires`, the default). A global grid
every day would be billions of rows, and the map only shows weather around fires,
so each month of weather keeps only the grid cells within ~100–150 km of that
month's fire hexagons (their H3 res-3 parents plus one ring;
`etl/include/weather/fires.py`), and only boxes of at most 10° around them are
requested from POWER. That decides *what* to download; *when*:

- Fire first. The weather DAG only plans days whose fires, loaded with the same
  area (`_firms_area` in gold), are already in gold, walking forward from where
  each weather product stopped and halting at the first gap of more than 7 days.
- Up to yesterday: days POWER hasn't published yet (~2 days) come back empty and
  are asked again the next day. A month never runs with only part of its fires;
  it's picked up by the next daily run once FIRMS has them.
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
(hours of downloads, tens of GB of fire data plus little weather: daily values near
the fires; the weather follows as the world's fires land).

## Loading files you already downloaded

If you have FIRMS CSVs from somewhere else (the Area API, one file per request), you can put them in the lake
without downloading anything again. Any folder layout works; the product in the file name (`MODIS_SP`,
`VIIRS_NOAA20_SP`, `VIIRS_SNPP_NRT`, ...) decides the bronze table, and files that aren't a FIRMS CSV (wrong
header, no rows, no product in the name) are skipped and reported.

```bash
make etl-load-local DIR=/path/to/csvs ARGS="--dry-run"     # what it found and what it would load; touches nothing
make etl-load-local DIR=/path/to/csvs                      # load it
make etl-reprocess                                         # silver + gold from the raw data (Airflow running)
```

It stages the files as Parquet (typed, the same detection found in several files kept once) and appends them
with the same dlt pipeline as the API loads, so the rows are indistinguishable from API loads: dbt keeps the
latest version of each detection, whatever its origin. Lineage columns say where each row came from:
`_firms_url` is `local-file://<path relative to DIR>`, `_firms_area` the bbox of the file (see below) or
`local:<label>`, `_extracted_at` the file's modification time.

- **Load order matters.** Silver keeps, for each detection, the row of the *latest load*, not of the newest data:
  a file loaded after an API load replaces that API row if both describe the same detection (same place, time and
  satellite). Load old files first, or load only what the lake doesn't have (`--since`/`--until`, `--product`).
- **Large backfills: load and build by year.** Tested with 12.6 M detections (9,281 files, 2012–2025, 4 products):
  the load takes ~3 min, but one `dbt build --full-refresh` over everything runs out of memory (even with 5 GB).
  Slicing works: for each year run `--since YYYY-01-01 --until YYYY-12-31`, then an incremental `dbt build`
  (the models are incremental by month). Measured per slice (~3–4 M rows): load 50–65 s, ~2 GB peak;
  dbt 65–140 s, 4–5.6 GB peak (51/51 models pass). Load years oldest first (see load order).
- **Record what was downloaded, not only what has fire.** A window with no detections leaves no row, so "no
  detection" cannot be told from "no data" without a record of the windows. The loader registers every dated file,
  header-only ones included, in `firms_raw.download_windows`, and dbt (`fct_area_coverage_monthly`) turns it into
  complete months per area. Files named with only a start date need `--window-days N` (the days that window
  covers, capped at the end of the year): the downloads in `firms/year/` are 5-day windows, so
  `--root firms/year --window-days 5`. `--windows-only` records the windows without loading detections (to backfill).
- Files already loaded (same path, size and modification time) are skipped; the register is the table
  `firms_raw.local_files_loaded`. `--force` loads them again.
- `--areas areas.json` fills `_firms_area` from the label in front of the product in the file name
  (`{"AR_north": "-73.6,-55,-53.6,-21.8"}`); without it the label is kept as `local:AR_north`.
- Narrow the load with `--product`, `--prefix` (that label), `--since`/`--until` (days written in the file name)
  and `--exclude` (globs). `make etl-load-local ARGS="--help"` lists everything.
- Airflow must not be writing to the lake meanwhile (the catalog accepts one writer): pause the ingest and dbt
  DAGs or stop Airflow first. DuckDB creates folders with mode 755 whatever the umask, so the loader opens the permissions
  of what it wrote at the end (otherwise Airflow, another user, fails with "Permission denied"); after any other terminal
  write (local dbt) run `make etl-lake-perms`.
- The catalog keeps the data path of whoever wrote first (inside Airflow it is `/usr/local/airflow/data/lake/files/`); every
  connection from outside Docker overrides it (`OVERRIDE_DATA_PATH`), so it does not matter who writes first.
- The loaded days aren't limited to the dev dataset (South America, 2025 onward): the lake takes any area
  and period, and the scheduled runs go on as before.

## Harmonized data (MODIS and VIIRS on equal terms)

MODIS and VIIRS do not see the same fire the same way: VIIRS has ~4x the detections per hexagon (smaller pixel) and
MODIS inflates its FRP at the scan edge (bow-tie). The `firms_dbt_harmonize` DAG builds, on top of silver and gold, the
layers that put both in the same units (MODIS units). All of it is dbt, tagged `harmonized`
(`etl/dags/dbt/firms/models/harmonized/`), and runs by itself after `firms_dbt_sp` / `firms_dbt_nrt` (they update the
`firms_gold` asset). First build or after a change: `make etl-harmonize`.

| Model | What |
|---|---|
| seed `k_cell` | VIIRS S-NPP / MODIS factor per H3 cell (FRP and counts), **frozen**: hierarchical shrinkage global -> r2..r6, chosen by year-parity cross-validation. Calibrated on 2013-2025 where both sensors have complete data (4 areas: delta, Iberá, Zambia, AR_north); elsewhere the cell's ancestor or the global k. Scripts and method: `data_exploration/k_cell/` |
| `hrm_cell_k` | k of every hexagon: own cell > nearest ancestor > global (`k_res` says which) |
| `hrm_modis_merged` | MODIS with bow-tie duplicates merged (same pass, within half a pixel along track and along scan) and FRP corrected for pixel size |
| `fct_hexagon_harmonized_daily` | hexagon x day x sensor, both in MODIS units (VIIRS S-NPP divided by k). Feeds the Compare view, the calendar and the hexagon report |
| `fct_hexagon_harmonized_monthly` | the one series across the 2012 change: MODIS before 2012-02, VIIRS S-NPP / k after |
| `fct_area_coverage_monthly` | which area-months were really downloaded; tells "no detection" from "no data". Needs `firms_raw.download_windows` (see above) |
| seed `reliability_model`, `hrm_detection_reliability`, `fct_hexagon_reliability_monthly` | persistence (neighbours of the same sensor within 1.5 km and 1-36 h) and the reliability index: P(an independent sensor confirms the detection), logistic on confidence, persistence and FRP, frozen coefficients (`data_exploration/reliability/`) |

Limits worth knowing: only S-NPP is on the VIIRS side (k was calibrated on it); the k outside the 4 calibrated areas is a
regional or global guess; the reliability index is a proxy (confirmation by another sensor, out-of-fold AUC 0.66 MODIS /
0.73 VIIRS, 2019 only), not truth. A huge first build of `hrm_detection_reliability` can run by month:
`dbt build -s hrm_detection_reliability --vars '{only_months: ["2019-01-01", "2019-02-01"]}'`.

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
