# MODIS · VIIRS fire detections

NASA Space Apps 2026. NASA FIRMS active-fire detections from **MODIS** and
**VIIRS**, ingested into a local lake and shown on a map.

```
FIRMS API ──▶ etl/ (Airflow: dlt + dbt) ──▶ data/lake (DuckLake) ──▶ backend/ (FastAPI) ──▶ frontend/ (Vue map)
```

| Folder | What | Docs |
|---|---|---|
| `etl/` | Airflow (Astro): FIRMS ingestion with dlt, transformations with dbt | Each DAG's docs in the Airflow UI |
| `data/` | The lake: bronze / silver / gold around the H3 hexagon, plus the dev dataset | [data/README.md](data/README.md) |
| `backend/` | API over the gold layer (DuckDB + spatial + H3) | [backend/README.md](backend/README.md) |
| `frontend/` | Map: hexagons, drawn zones, burn calendar, time slider, MODIS \| VIIRS curtain | [frontend/README.md](frontend/README.md) |

## Requirements

- Docker
- [Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli) (runs Airflow locally)
- [uv](https://docs.astral.sh/uv/) ≥ 0.9 (`uv self update` if older)
- Node.js ≥ 20.19
- A FIRMS MAP_KEY: free, request it at https://firms.modaps.eosdis.nasa.gov/api/map_key/

## First time

From the repo root:

```bash
# 1. FIRMS key and area (edit etl/.env and replace YOUR_MAP_KEY)
cp etl/.env.example etl/.env

# 2. Install everything and prepare data/
make etl-setup
make backend-setup
make frontend-setup

# 3. Start Airflow (first build takes a few minutes)
make etl-start

# 4. Load the dev dataset: South America, 2025 to today (~2 h, runs in Airflow)
make etl-seed-dev
```

Follow the load at **http://etl.localhost:6563** (Airflow UI; `make etl-start` prints the URL).
The map works while it loads; it just shows what has landed so far.

## Every day

Three terminals, from the repo root:

```bash
make etl-start        # 1. Airflow (skip if already running) → http://etl.localhost:6563
make backend-dev      # 2. API                              → http://localhost:8000/docs
make frontend-dev     # 3. App                              → http://localhost:5173
```

Open **http://localhost:5173**. Stop with `Ctrl+C` (backend, frontend) and `make etl-stop` (Airflow).

Once the dev dataset is loaded, Airflow keeps it current on its own: NRT every
day, SP every week (each new month NASA publishes).

`make` alone lists every command.

## Troubleshooting

| Problem | Fix |
|---|---|
| Frontend shows `ECONNREFUSED 127.0.0.1:8000` | The backend isn't running: `make backend-dev` |
| `uv sync` fails with `Required uv version >=0.9` | `uv self update` |
| Airflow tasks fail with `Permission denied` on `data/` | Run `make etl-setup` again (it sets the folder permissions) |
| `conn_id firms_default isn't defined` | `etl/.env` is missing the key line: copy it from `etl/.env.example`, then `make etl-restart` |
| The `duckdb` CLI can't open the lake | Use DuckDB 1.5.6 (same as the project); see [data/README.md](data/README.md) for the `ATTACH` |
| macOS | The lake catalog uses SQLite WAL, which isn't shared across Docker Desktop's VM: run the backend in a container too |
