# MODIS · VIIRS fire detections

NASA Space Apps 2026. NASA FIRMS active-fire detections from **MODIS** and
**VIIRS**, with the weather around them (NASA Earthdata: MERRA-2 and GPM IMERG),
ingested into a local lake and shown on a map.

```
FIRMS API ───────┐
                 ├──▶ etl/ (Airflow: dlt + dbt) ──▶ data/lake (DuckLake) ──▶ backend/ (FastAPI) ──▶ frontend/ (Vue map)
NASA Earthdata ──┘
```

| Folder | What | Docs |
|---|---|---|
| `etl/` | Airflow (Astro): FIRMS and weather ingestion with dlt, transformations with dbt | Each DAG's docs in the Airflow UI |
| `data/` | The lake: bronze / silver / gold around the H3 hexagon, plus the dev dataset | [data/README.md](data/README.md) |
| `backend/` | API over the gold layer (DuckDB + spatial + H3) | [backend/README.md](backend/README.md) |
| `frontend/` | Map: hexagons, drawn zones, burn calendar, time slider, MODIS \| VIIRS curtain | [frontend/README.md](frontend/README.md) |

## Setup guide: tools to install

Install these once per machine, before anything under [First time](#first-time).
The commands work on Linux and macOS. **On Windows, use WSL2 (Ubuntu) and follow the
Linux steps inside it**: the Makefile relies on `make`, `bash` and `setfacl`.

| Tool | Version | What for |
|---|---|---|
| Git | any | Clone the repo |
| make | any | Every command in this README is a `make` target |
| Docker (with Compose v2) | recent | Airflow runs in containers |
| [Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli) | recent | Starts Airflow locally (`make etl-*`) |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.9 | Python venvs for `etl/` and `backend/`. Also installs Python 3.14 |
| [Node.js](https://nodejs.org/) + npm | 22 LTS (≥ 20.19) | Frontend (Vue + Vite) |
| acl (`setfacl`) | any | **Linux only**: lets your user read what Airflow writes in `data/` |
| FIRMS MAP_KEY | — | Free key to download from NASA FIRMS |
| NASA Earthdata Login | — | Free account to download the weather (MERRA-2, GPM IMERG) |

You don't need to install Python yourself: uv downloads the version pinned in
`.python-version` (3.14) the first time you run `make etl-setup` or `make backend-setup`.

### 1. Git, make and acl

```bash
# Ubuntu / Debian / WSL2
sudo apt update && sudo apt install -y git make acl curl

# macOS (git and make come with the Command Line Tools; acl isn't needed)
xcode-select --install
```

### 2. Docker

- **Linux**: install [Docker Engine](https://docs.docker.com/engine/install/), then let
  your user run it without `sudo` (log out and back in afterwards):
  ```bash
  sudo usermod -aG docker $USER
  ```
- **macOS / Windows**: install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  and leave it running. On Windows, turn on its WSL2 integration for your distro.

Give Docker at least 4 GB of RAM (Docker Desktop: Settings → Resources): Airflow
runs five containers.

### 3. Astro CLI

```bash
# Linux / WSL2
curl -sSL install.astronomer.io | sudo bash -s

# macOS
brew install astro
```

### 4. uv

```bash
# Linux / macOS / WSL2
curl -LsSf https://astral.sh/uv/install.sh | sh

# Already installed but older than 0.9?
uv self update
```

### 5. Node.js

The simplest way is [nvm](https://github.com/nvm-sh/nvm), which installs Node
without `sudo` and lets you switch versions:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
# open a new terminal, then:
nvm install 22
```

On macOS, `brew install node@22` works too.

### 6. FIRMS MAP_KEY

Request it (free, it arrives by email) at
https://firms.modaps.eosdis.nasa.gov/api/map_key/. You'll paste it into `etl/.env`
in the next section. Don't commit it.

### 7. NASA Earthdata Login

Create a free account at https://urs.earthdata.nasa.gov/users/new, then approve
the two applications the weather DAG goes through (a script can't click "Approve":
until you do it once in a browser, every download answers **403**):

1. In your profile, **Applications → Authorized Apps → Approve More Applications**:
   approve **NASA GESDISC DATA ARCHIVE** (MERRA-2 and IMERG are GES DISC data).
2. Open this file once in the browser, logged in to Earthdata, and approve what it
   asks for (OPeNDAP, which cuts the files to the area): it should end up showing
   an XML document.
   https://opendap.earthdata.nasa.gov/collections/C1276812863-GES_DISC/granules/M2T1NXSLV.5.12.4%3AMERRA2_400.tavg1_2d_slv_Nx.20250101.nc4.dmr

Your username and password go into `etl/.env` in the next section.

### Optional: DuckDB CLI

Only needed to query the lake by hand (see [data/README.md](data/README.md)). It
must be **exactly 1.5.6**, the version the ETL writes with: download it from
[the v1.5.6 release](https://github.com/duckdb/duckdb/releases/tag/v1.5.6).

### Check everything

```bash
git --version
make --version
docker --version && docker compose version
docker run --rm hello-world     # Docker works without sudo
astro version
uv --version                    # >= 0.9
node --version                  # >= 20.19
setfacl --version               # Linux only
```

If every command prints a version, continue with [First time](#first-time).

## First time

From the repo root:

```bash
# 1. Keys and area: edit etl/.env and replace YOUR_MAP_KEY and the YOUR_EARTHDATA_* values
cp etl/.env.example etl/.env

# 2. Install everything and prepare data/
make etl-setup
make backend-setup
make frontend-setup

# 3. Start Airflow (first build takes a few minutes)
make etl-start

# 4. Load the dev dataset: South America, 2025 to today (~2 h, runs in Airflow)
make etl-seed-dev

# 5. Load its weather: same area and dates (runs in Airflow, queued after the fire loads)
make etl-seed-weather-dev
```

Fire and weather cover the same area and dates. If you change either, change
both: see [data/README.md](data/README.md#changing-the-region-or-the-dates).

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
day, SP every week (each new month NASA publishes), weather every day (whatever
NASA has published since the last run).

`make` alone lists every command.

## Troubleshooting

| Problem | Fix |
|---|---|
| Frontend shows `ECONNREFUSED 127.0.0.1:8000` | The backend isn't running: `make backend-dev` |
| `uv sync` fails with `Required uv version >=0.9` | `uv self update` |
| Airflow tasks fail with `Permission denied` on `data/` | Run `make etl-setup` again (it sets the folder permissions) |
| `conn_id firms_default isn't defined` | `etl/.env` is missing the key line: copy it from `etl/.env.example`, then `make etl-restart` |
| `conn_id earthdata_default isn't defined` | Same for the Earthdata line (`AIRFLOW_CONN_EARTHDATA_DEFAULT`), then `make etl-restart` |
| Weather tasks fail with `Earthdata refused the request (401 …)` | Wrong username/password in `etl/.env`, then `make etl-restart` |
| Weather tasks fail with `Earthdata refused the request (403 …)` | An application not approved yet in your Earthdata account: do both parts of [step 7](#7-nasa-earthdata-login), then clear the failed task |
| The hexagon panel shows weather but no temperature or wind for the last weeks | Expected: MERRA-2 is published weeks late; precipitation (IMERG Late) is there |
| The `duckdb` CLI can't open the lake | Use DuckDB 1.5.6 (same as the project); see [data/README.md](data/README.md) for the `ATTACH` |
| macOS | The lake catalog uses SQLite WAL, which isn't shared across Docker Desktop's VM: run the backend in a container too |
