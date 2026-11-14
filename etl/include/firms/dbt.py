"""Cosmos configuration shared by the FIRMS dbt DAGs."""

import logging

from cosmos import ExecutionConfig, ProfileConfig, ProjectConfig

from include.firms.config import DBT_EXECUTABLE, DBT_PROJECT_PATH, LAKE_WRITER_POOL, ensure_catalog

# dbt attaches the DuckLake catalog, which must be in WAL mode before anyone
# opens it (see ensure_catalog). Idempotent, and runs wherever the dbt DAGs are
# loaded, including the worker right before each dbt task. A failure here must
# not break DAG parsing: dbt would then fail with a clear error of its own.
try:
    ensure_catalog()
except Exception as e:  # noqa: BLE001 - e.g. OSError, sqlite3 "database is locked"
    logging.getLogger(__name__).warning("Could not prepare the lake catalog: %s", e)

project_config = ProjectConfig(DBT_PROJECT_PATH)

profile_config = ProfileConfig(
    profile_name="firms",
    target_name="dev",
    profiles_yml_filepath=DBT_PROJECT_PATH / "profiles.yml",
)

execution_config = ExecutionConfig(dbt_executable_path=str(DBT_EXECUTABLE))

# Every dbt model writes to the lake: one writer at a time (see config.py).
operator_args = {"pool": LAKE_WRITER_POOL}
