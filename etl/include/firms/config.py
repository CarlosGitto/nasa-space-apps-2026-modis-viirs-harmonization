"""Settings shared by the FIRMS ingest and dbt DAGs.

Everything lives in the Parquet lake; DuckDB is only the engine dbt runs on.
- bronze (dlt): immutable, append-only.
  {FIRMS_LAKE_PATH}/firms_raw/{table}/load_date=YYYY-MM-DD/{load_id}.{file_id}.parquet
  Every load is kept, so this is the versioned history; `_dlt_loads` in the
  same folder records each load.
- silver / gold (dbt, `materialized: external`): rebuilt on each dbt run.
  {FIRMS_LAKE_PATH}/silver/{table}/data_N.parquet
  {FIRMS_LAKE_PATH}/gold/{table}/data_N.parquet
- firms.duckdb: views over silver/gold, so dbt tests and ad-hoc queries work.
Any engine (DuckDB, Spark, Trino, a warehouse...) can read the lake directly.

Gotcha: when a silver/gold table has no rows, dbt-duckdb writes one all-NULL
row so the Parquet keeps its schema (its DuckDB view filters it out). Engines
reading the Parquet directly should filter `_dlt_load_id is not null`.
"""

import os
from pathlib import Path

from airflow.sdk import Asset

AIRFLOW_HOME = Path(os.getenv("AIRFLOW_HOME", "/usr/local/airflow"))

# The repo-level `data/` folder (outside etl/), mounted here by
# etl/docker-compose.override.yml so the backend and other services share it.
DATA_DIR = AIRFLOW_HOME / "data"

# Local folder by default; can be a bucket URL (s3://..., gs://...) later.
# dbt's sources.yml reads the same env var with the same default.
LAKE_PATH = os.getenv("FIRMS_LAKE_PATH", str(DATA_DIR / "lake"))
RAW_DATASET = "firms_raw"
LAKE_LAYOUT = "{table_name}/load_date={YYYY}-{MM}-{DD}/{load_id}.{file_id}.{ext}"
# Folders dbt writes into (its schema names, see dbt_project.yml).
DBT_LAYERS = ("silver", "gold")

# dbt's profiles.yml reads the same env var with the same default.
DUCKDB_PATH = os.getenv("FIRMS_DUCKDB_PATH", str(DATA_DIR / "firms.duckdb"))

# DuckDB allows a single writer: every dbt task uses this 1-slot pool
# (defined in airflow_settings.yaml for local dev).
DUCKDB_POOL = "duckdb"

# HTTP connection whose password is the FIRMS MAP_KEY.
FIRMS_CONN_ID = "firms_default"

# Updated by the ingest DAGs, consumed by the dbt DAGs.
FIRMS_RAW_NRT = Asset("firms_raw_nrt")
FIRMS_RAW_SP = Asset("firms_raw_sp")

DBT_PROJECT_PATH = AIRFLOW_HOME / "dags" / "dbt" / "firms"
DBT_EXECUTABLE = AIRFLOW_HOME / "dbt_venv" / "bin" / "dbt"  # created in the Dockerfile


def get_map_key() -> str:
    from airflow.sdk import BaseHook

    map_key = BaseHook.get_connection(FIRMS_CONN_ID).password
    if not map_key:
        raise ValueError(f"Connection '{FIRMS_CONN_ID}' has no password (FIRMS MAP_KEY)")
    return map_key


def ensure_lake_layout() -> None:
    """Create the lake's layer folders on local disk (no-op for bucket URLs).

    DuckDB's COPY doesn't create parent folders, so dbt's external models need
    lake/silver and lake/gold to exist before they write their table folders.

    The raw dataset folder (firms_raw) is NOT created here: dlt creates it on
    its first load, and fails if the folder exists without its `init` marker.
    """
    if "://" in LAKE_PATH:
        return
    for layer in DBT_LAYERS:
        os.makedirs(os.path.join(LAKE_PATH, layer), exist_ok=True)


def raw_pipeline(name: str):
    """dlt pipeline that appends Parquet files to the raw lake."""
    import dlt

    ensure_lake_layout()
    return dlt.pipeline(
        pipeline_name=name,
        destination=dlt.destinations.filesystem(bucket_url=LAKE_PATH, layout=LAKE_LAYOUT),
        dataset_name=RAW_DATASET,
    )


def last_loaded_dates(tables: list[str]) -> dict[str, "date"]:  # noqa: F821
    """table -> max(acq_date) found in the lake, for tables that have files."""
    import glob

    import duckdb

    out = {}
    with duckdb.connect() as con:  # in-memory: reads Parquet, no lock on the warehouse
        for table in tables:
            pattern = f"{LAKE_PATH}/{RAW_DATASET}/{table}/*/*.parquet"
            if "://" not in LAKE_PATH and not glob.glob(pattern):
                continue
            last = con.execute(f"select max(acq_date) from read_parquet('{pattern}')").fetchone()[0]
            if last is not None:
                out[table] = last
    return out
