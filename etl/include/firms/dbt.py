"""Cosmos configuration shared by the FIRMS dbt DAGs."""

import logging

from cosmos import ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig

from include.firms.config import DBT_EXECUTABLE, DBT_PROJECT_PATH, FIRMS_GOLD, LAKE_WRITER_POOL, ensure_catalog

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


def render_config(select: list[str] | None = None, exclude: list[str] | None = None) -> RenderConfig:
    """Which models a DAG renders (dbt selectors; None = every model; `exclude` takes some out).

    Cosmos runs each model's tests right after it. A test that reads two models
    (`relationships` from a gold fact to `dim_hexagon`) would then run before
    `dim_hexagon` exists, which is built from those facts: on a fresh lake the
    first run fails. Detached, such a test is its own task, after both models.
    """
    return RenderConfig(select=select or [], exclude=exclude or [], should_detach_multiple_parents_tests=True)


def emit_gold_when_done(dag) -> None:
    """Add a last task that updates the `firms_gold` asset once every task of the DAG has finished.

    A dbt DAG is a set of Cosmos tasks with no single end: this one hangs from all of the leaves, so whatever is
    scheduled on `firms_gold` (the harmonization) starts only when silver and gold are complete.
    """
    from airflow.providers.standard.operators.empty import EmptyOperator

    leaves = list(dag.leaves)
    with dag:
        done = EmptyOperator(task_id="firms_gold_ready", outlets=[FIRMS_GOLD])
    for leaf in leaves:
        leaf >> done
