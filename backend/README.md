# backend

Minimal API for the frontend: FastAPI + DuckDB (with the `spatial` and `h3`
extensions) reading the **gold** layer of the lake, a DuckLake written by `etl/`
(`data/lake/`, see [data/README.md](../data/README.md)). It attaches the lake
read-only: no database of its own, new ETL runs show up on the next request,
and a half-written table is never seen.

## Run

From the repo root (needs uv ≥ 0.9):

```bash
make backend-setup   # once
make backend-dev     # http://localhost:8000, interactive docs at /docs
make backend-test
```

| Env var | Default |
|---|---|
| `LAKE_PATH` | `<repo>/data/lake` (DuckLake: `catalog.sqlite` + `files/`) |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` |

The first start downloads the DuckDB `spatial`, `h3`, `ducklake` and `sqlite`
extensions (cached in `~/.duckdb`). If the lake doesn't exist yet the API still
starts, with every dataset `available: false`.

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `GET /datasets` | Every dataset with `available`, `rows` (wildfire detections), `start_date`, `end_date` |
| `GET /datasets/{dataset}/detections` | GeoJSON points, most recent first, each with `product` and `is_static_source`. `limit` (default 5000, max 50000); `truncated: true` when more matched |
| `GET /datasets/{dataset}/stats/daily` | Detections, day/night and FRP per date |
| `GET /datasets/{dataset}/stats/monthly` | Detections and FRP per year and month (burn calendar) |
| `GET /datasets/{dataset}/stats/hex` | GeoJSON H3 hexagons with detections and FRP, resolution `res` (0–6: 6 is the base cell, coarser levels are its parents). Cells crossing the antimeridian are dropped |
| `GET /datasets/{dataset}/stats/grid` | GeoJSON polygons with detections and FRP per `cell` degrees (0.1–10) |
| `GET /hexagons/{h3}` | One res-6 hexagon: identity (center, area, boundary, parents, static source) and its **whole-history profile** per sensor |
| `GET /hexagons/{h3}/monthly` | Its monthly series per sensor and satellite |
| `GET /hexagons/{h3}/period` | Its profile for `start`..`end` per sensor: totals, per satellite, daily series |
| `GET /hexagons/{h3}/detections` | Its detections as a table for a `dataset` and `start`/`end` (MODIS/VIIRS datasets only). `limit` (default 2000) |
| `GET /hexagons/{h3}/weather` | Its daily weather for `start`..`end` (default: the last 90 days up to yesterday, at most 731), asked to **NASA POWER on demand** for the hexagon's center and cached: temperature, dew point, humidity, VPD, wind, precipitation with its 7 / 30-day totals and days since rain. Any hexagon on Earth. 503 if POWER doesn't answer |
| `GET /weather/map` | One UTC day (`date`) of a weather `layer` for the map, inside `bbox`, from the lake (NASA POWER daily near fires): `temperature` (daily max, °C), `precipitation` (daily total, mm; dry cells < 0.1 mm left out) or `wind` (daily mean speed, m/s, and the direction it comes from). Compact `[lon, lat, value(, deg)]` cell centers plus the cell size; big areas are averaged into coarser cells (~20 k at most). With no cells, `coverage` says which days the layer has |
| `POST /hexagons/selection/weather` | The map's weather (max temperature, rain, mean wind) of the POWER cells under the selected hexagons (`{"h3": [...], "start", "end"}`, up to 366 days), every day, on demand from NASA POWER and cached: point requests for a few scattered cells, regional ones (per parameter and 10° box) for many cells together, whichever takes fewer requests |
| `POST /hexagons/selection/summary` | Many hexagons at once (`{"h3": [...], "start", "end"}`, up to 5000): per hexagon with any history, its totals and its detections in the dates, per sensor, busiest first |
| `POST /hexagons/selection/detections` | Detections of many hexagons as one table (`{"h3": [...], "dataset", "start", "end", "limit"}`) |

Common filters (all optional; without them the whole world and all dates):

- `start`, `end`: dates, inclusive.
- `bbox=minLon,minLat,maxLon,maxLat`: e.g. the map viewport.
- `zone`: a GeoJSON `Polygon`/`MultiPolygon` geometry (e.g. drawn by the user), as a JSON string. Combines with `bbox`.
- `daynight=D|N`.

`detections` always counts **wildfires**. Heat from static sources (refineries,
gas flares, industry, volcanoes) is reported apart as `static_detections`, and
`/detections` returns those points flagged with `is_static_source`. On the
aggregated endpoints (hex, daily, monthly) `bbox` and `zone` select hexagons by
their center: the hexagon is the unit.

The `/hexagons/{h3}` endpoints are lookups by key on precomputed gold tables
(`fct_hexagon_profile`, `fct_hexagon_monthly`, `fct_hexagon_daily`), sorted by
hexagon: tens of milliseconds on the dev lake. They use the continuous series
(SP + NRT), one profile per sensor. An id that is not a valid res-6 cell gives
422; a hexagon without data, 404.

The hexagon's weather comes from [NASA POWER](https://power.larc.nasa.gov) on demand
(`app/power.py`): one point request per place, cached per POWER grid cell (0.5° ×
0.625°) and day in `data/cache/power.sqlite` (`POWER_CACHE_PATH`), recent days
refreshed at most every 6 hours; one request at a time, a second apart. POWER is ~2
days behind real time, so the newest days can be null. VPD and minimum relative
humidity are derived from temperature and dew point (Magnus). A rain day is >=
`rain_day_mm` (1 mm). The map's weather layers read the lake instead (see
[data/README.md](../data/README.md#weather-nasa-power)).

The OpenAPI schema (`/openapi.json`) is typed, so the frontend can generate its client from it.

## Datasets: MODIS, VIIRS and harmonized

`{dataset}` is `<sensor>_<product>`: sensor `modis | viirs | harmonized`, product:

| Product | What |
|---|---|
| `best` | One continuous series: SP where NASA has published it, NRT for the days after (`is_preferred` in gold). The default in the app |
| `sp` | Standard processing (science quality, published ~2–3 months later) |
| `nrt` | Near real time (within hours, provisional) |

MODIS and VIIRS read the hexagon gold tables (`fct_hexagon_detections`,
`fct_hexagon_daily`, `dim_hexagon`, see [data/README.md](../data/README.md));
a dataset is a filter on them. A dataset with no data yet is listed as
`available: false` and its endpoints return 404.

Every detection has the same properties whatever the sensor: `sensor`,
`satellite`, `acq_datetime_utc`, `frp`, `confidence`, `daynight`, `brightness`.
`confidence` is text because the scales differ (MODIS 0–100, VIIRS `l/n/h`).

**`harmonized_best`** is the harmonized series as one selectable dataset: MODIS before 2012-02, VIIRS S-NPP divided by the
k of each hexagon from then on, in MODIS units (`fct_hexagon_harmonized_daily`; counts are fractional). It goes through the
generic endpoints (`/datasets/harmonized_best/stats/hex`, `/daily`, `/monthly`, detections), so the map, the calendar and the
panels use it like any other dataset. Its detections (zoomed-in points, a hexagon's list) are the raw ones behind the series:
MODIS before the splice, S-NPP only after it. `modis_harmonized` / `viirs_harmonized` are the two sides of the Compare view in
MODIS units; the UI does not list them.

**Contract for the harmonized data**: a table or view `gold.harmonized_nrt` /
`gold.harmonized_sp` in the same DuckLake catalog, with:

- Required columns: `latitude`, `longitude`, `acq_date` (date), `acq_datetime_utc` (timestamp), `frp`, `daynight` (`D`/`N`).
- Optional: `satellite`, `confidence`, `brightness`. Missing ones are returned as `null`.

Write it from Airflow with the `ducklake` pool (the SQLite catalog takes one
writer at a time), e.g. as dbt models in `etl/dags/dbt/firms/`.

If the column names end up different, adjust the `_HARMONIZED` mapping in
[app/db.py](app/db.py). Endpoints and responses do not change.
