# data/

Local data shared by the repo's services. Written by the ETL (`etl/`), read by
anyone else (backend, notebooks...). Not committed to git, except this README.

## Layout: a DuckLake

```
data/lake/
├── catalog.sqlite (+ -wal, -shm)   # DuckLake catalog: which files make up each table and version
└── files/                          # Parquet files managed by DuckLake (don't edit by hand)
    ├── firms_raw/<table>/...       # bronze (dlt): append-only, every load kept
    └── silver/<table>/acq_month=.../  # silver (dbt): incremental by acquisition month
```

| Schema | What | Written by |
|---|---|---|
| `firms_raw` | Bronze: raw FIRMS detections, every load as new rows (`_dlt_load_id`) | dlt (`firms_ingest_nrt`, `firms_ingest_sp`) |
| `silver` | Normalized, latest version per detection, invalid rows flagged in `dq_issue` | dbt (incremental) |
| `gold` | Views over silver with only the valid rows: what consumers read | dbt |

Tables: `modis_nrt`, `viirs_nrt`, `modis_sp`, `viirs_sp` (NRT = near real time,
SP = standard / science-quality).

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

SELECT * FROM lake.gold.viirs_nrt LIMIT 10;

-- Time travel
SELECT * FROM lake.snapshots();
SELECT count(*) FROM lake.gold.modis_sp AT (VERSION => 42);
```

Always attach `READ_ONLY` unless you are the ETL: the SQLite catalog takes a
single writer at a time.

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
