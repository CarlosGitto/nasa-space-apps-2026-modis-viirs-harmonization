"""Settings shared by the FIRMS ingest, dbt and maintenance DAGs.

All layers live in one DuckLake: Parquet files plus a catalog that records which
files make up each table and each version (snapshot). That gives atomic writes
(readers never see half-written tables), incremental updates and time travel.

    {FIRMS_LAKE_PATH}/catalog.sqlite      DuckLake catalog (SQLite, WAL mode)
    {FIRMS_LAKE_PATH}/files/              Parquet files managed by DuckLake
        firms_raw/<table>/...             bronze (dlt): append-only, every load kept
        silver/<table>/acq_month=.../     silver (dbt): incremental by acquisition month
    gold                                  views over silver (dbt), no files of their own

Attached as `lake`, so tables are `lake.firms_raw.*`, `lake.silver.*`, `lake.gold.*`.

Concurrency: the SQLite catalog takes one writer at a time (two concurrent
commits fail with "database is locked"), so every task that writes to the lake
(dlt loads, dbt models, maintenance) uses the 1-slot LAKE_WRITER_POOL. Readers
(the backend) never block writers thanks to WAL mode.
"""

import os
from pathlib import Path

from airflow.sdk import Asset

AIRFLOW_HOME = Path(os.getenv("AIRFLOW_HOME", "/usr/local/airflow"))

# The repo-level `data/` folder (outside etl/), mounted here by
# etl/docker-compose.override.yml so the backend and other services share it.
DATA_DIR = AIRFLOW_HOME / "data"

# dbt's profiles.yml reads the same env var with the same default.
LAKE_PATH = os.getenv("FIRMS_LAKE_PATH", str(DATA_DIR / "lake"))
CATALOG_PATH = f"{LAKE_PATH}/catalog.sqlite"
LAKE_DATA_PATH = f"{LAKE_PATH}/files/"
LAKE_ALIAS = "lake"
RAW_DATASET = "firms_raw"

# Single writer for the DuckLake catalog (defined in airflow_settings.yaml for local dev).
LAKE_WRITER_POOL = "ducklake"

# Area the ingest DAGs request from FIRMS by default: "world" or a "west,south,east,north"
# bbox. Set FIRMS_AREA in etl/.env to work on a region (e.g. the local dev dataset), so the
# scheduled NRT/SP runs keep the same coverage as the seeded data.
FIRMS_AREA = os.getenv("FIRMS_AREA", "world")

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


def ensure_catalog() -> None:
    """Create the lake folder and put the SQLite catalog in WAL mode (idempotent).

    WAL lets readers (the backend) keep the catalog open while a writer commits;
    with SQLite's default journal a reader blocks commits. The setting is stored
    in the file, so it only has to happen once; DuckLake initializes its tables
    in this (possibly empty) SQLite file on first attach.
    """
    import sqlite3

    os.makedirs(LAKE_DATA_PATH, exist_ok=True)
    con = sqlite3.connect(CATALOG_PATH)
    try:
        con.execute("PRAGMA journal_mode=WAL")
    finally:
        con.close()
    # Airflow (uid 50000) and the backend (host user) both open the catalog, and
    # in WAL mode every opener writes to the -shm file. SQLite creates -wal/-shm
    # with the same permissions as the catalog, so make it writable for both.
    try:
        os.chmod(CATALOG_PATH, 0o666)
    except PermissionError:
        pass  # not the owner: whoever created it already set the mode


def attach_sql(read_only: bool = False) -> str:
    """ATTACH statement for the lake (needs the ducklake and sqlite extensions)."""
    options = f"DATA_PATH '{LAKE_DATA_PATH}'" + (", READ_ONLY" if read_only else "")
    return f"ATTACH 'ducklake:sqlite:{CATALOG_PATH}' AS {LAKE_ALIAS} ({options})"


def raw_pipeline(name: str):
    """dlt pipeline that appends to the bronze tables of the lake."""
    import dlt
    from dlt.destinations.impl.ducklake.configuration import DuckLakeCredentials

    ensure_catalog()
    credentials = DuckLakeCredentials(LAKE_ALIAS, catalog=f"sqlite:///{CATALOG_PATH}", storage=LAKE_DATA_PATH)
    return dlt.pipeline(
        pipeline_name=name,
        destination=dlt.destinations.ducklake(credentials=credentials),
        dataset_name=RAW_DATASET,
    )


def catalog_initialized() -> bool:
    """Whether DuckLake has created its tables in the catalog yet.

    ensure_catalog() creates an empty SQLite file (to set WAL mode) before the
    first write; a READ_ONLY attach refuses that file ("creating a new DuckLake
    is explicitly disabled"), so readers must check first.
    """
    import sqlite3

    if not os.path.exists(CATALOG_PATH):
        return False
    con = sqlite3.connect(f"file:{CATALOG_PATH}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'ducklake_metadata'"
        ).fetchone()
    finally:
        con.close()
    return row is not None


def last_loaded_dates(tables: list[str]) -> dict[str, "date"]:  # noqa: F821
    """table -> max(acq_date) in the bronze tables that exist and have rows."""
    import duckdb

    if not catalog_initialized():  # nothing loaded yet
        return {}
    out = {}
    with duckdb.connect() as con:
        con.execute(attach_sql(read_only=True))
        existing = {
            row[0]
            for row in con.execute(
                "select table_name from information_schema.tables where table_catalog = ? and table_schema = ?",
                [LAKE_ALIAS, RAW_DATASET],
            ).fetchall()
        }
        for table in tables:
            if table not in existing:
                continue
            last = con.execute(f'select max(acq_date) from {LAKE_ALIAS}.{RAW_DATASET}."{table}"').fetchone()[0]
            if last is not None:
                out[table] = last
    return out
