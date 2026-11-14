"""Cosmos configuration shared by the FIRMS dbt DAGs."""

import logging

from cosmos import ExecutionConfig, ProfileConfig, ProjectConfig

from include.firms.config import DBT_EXECUTABLE, DBT_PROJECT_PATH, DUCKDB_POOL, ensure_lake_layout

# dbt writes silver/gold into lake/<layer>/<table>; DuckDB can't create the
# layer folder itself. Idempotent, and runs wherever the dbt DAGs are loaded,
# including the worker right before each dbt task. A failure here must not
# break DAG parsing: dbt would then fail with a clear "No such file" error.
try:
    ensure_lake_layout()
except OSError as e:
    logging.getLogger(__name__).warning("Could not create lake layer folders: %s", e)

project_config = ProjectConfig(DBT_PROJECT_PATH)

profile_config = ProfileConfig(
    profile_name="firms",
    target_name="dev",
    profiles_yml_filepath=DBT_PROJECT_PATH / "profiles.yml",
)

execution_config = ExecutionConfig(dbt_executable_path=str(DBT_EXECUTABLE))

# Every dbt task writes to DuckDB, so it shares the single-writer pool.
operator_args = {"pool": DUCKDB_POOL}
