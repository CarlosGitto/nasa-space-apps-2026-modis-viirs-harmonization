# data/

Local data shared by the repo's services. Written by the ETL (`etl/`), read by
anyone else (backend, notebooks...). Not committed to git, except this README.

```
data/
├── lake/                                   # Parquet lake: the source of truth
│   ├── firms_raw/<product>/load_date=YYYY-MM-DD/<load_id>.parquet
│   │                                       # bronze (dlt): append-only, every load kept (versioned)
│   ├── firms_raw/_dlt_loads/               # one record per load (lineage)
│   ├── silver/<table>/data_0.parquet       # dbt: normalized, deduplicated, dq_issue flag
│   └── gold/<table>/data_0.parquet         # dbt: clean rows, ready to consume
└── firms.duckdb                            # DuckDB engine file: only views over the lake
```

Tables: `modis_nrt`, `viirs_nrt`, `modis_sp`, `viirs_sp` (NRT = near real time,
SP = standard / science-quality).

## Reading gold

Read the Parquet directly (any engine works), e.g. with DuckDB from the repo root:

```sql
select * from read_parquet('data/lake/gold/modis_nrt/*.parquet') limit 10;
```

The views in `firms.duckdb` point to paths inside the Airflow containers
(`/usr/local/airflow/data/...`), so from the host query the Parquet instead.

## Setup (once per machine)

Airflow runs as uid 50000 inside the containers and writes here through a bind
mount (`etl/docker-compose.override.yml`). Let it write, and keep your own user
able to read/delete what it creates:

```bash
mkdir -p data
chmod 777 data
setfacl -m u:$(id -u):rwx -d -m u:$(id -u):rwx data
```
