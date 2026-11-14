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
| `GET /datasets` | Every dataset with `available`, `rows`, `start_date`, `end_date` |
| `GET /datasets/{dataset}/detections` | GeoJSON points, most recent first. `limit` (default 5000, max 50000); `truncated: true` when more matched |
| `GET /datasets/{dataset}/stats/daily` | Detections, day/night and FRP per date |
| `GET /datasets/{dataset}/stats/monthly` | Detections and FRP per year and month (burn calendar) |
| `GET /datasets/{dataset}/stats/hex` | GeoJSON H3 hexagons with detections and FRP, resolution `res` (0–8). Cells crossing the antimeridian are dropped |
| `GET /datasets/{dataset}/stats/grid` | GeoJSON polygons with detections and FRP per `cell` degrees (0.1–10) |

Common filters (all optional; without them the whole world and all dates):

- `start`, `end`: dates, inclusive.
- `bbox=minLon,minLat,maxLon,maxLat`: e.g. the map viewport.
- `zone`: a GeoJSON `Polygon`/`MultiPolygon` geometry (e.g. drawn by the user), as a JSON string. Combines with `bbox`.
- `daynight=D|N`.

The OpenAPI schema (`/openapi.json`) is typed, so the frontend can generate its client from it.

## Datasets: MODIS, VIIRS and harmonized

`{dataset}` is `<sensor>_<product>`: sensor `modis | viirs | harmonized`,
product `nrt` (near real time) or `sp` (standard processing). Each one is the
table or view `gold.<dataset>` in the DuckLake. A dataset that isn't there yet
is listed as `available: false` and its endpoints return 404.

Every detection has the same properties whatever the sensor: `sensor`,
`satellite`, `acq_datetime_utc`, `frp`, `confidence`, `daynight`, `brightness`.
`confidence` is text because the scales differ (MODIS 0–100, VIIRS `l/n/h`).

**Contract for the harmonized data**: a table or view `gold.harmonized_nrt` /
`gold.harmonized_sp` in the same DuckLake catalog, with:

- Required columns: `latitude`, `longitude`, `acq_date` (date), `acq_datetime_utc` (timestamp), `frp`, `daynight` (`D`/`N`).
- Optional: `satellite`, `confidence`, `brightness`. Missing ones are returned as `null`.

Write it from Airflow with the `ducklake` pool (the SQLite catalog takes one
writer at a time), e.g. as dbt models in `etl/dags/dbt/firms/`.

If the column names end up different, adjust the `_HARMONIZED` mapping in
[app/db.py](app/db.py). Endpoints and responses do not change.
