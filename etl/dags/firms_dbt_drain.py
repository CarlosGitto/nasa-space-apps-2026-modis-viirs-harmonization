"""
## FIRMS dbt — drain a backlog

Runs `dbt build` over and over until nothing is pending. The incremental models take a few blocks of days (a week by default,
`firms_blocks_per_run` per run) and leave the rest pending, so that each run fits in memory; a long backlog (a large backfill, or dbt
failing for a while) needs many runs. Trigger this DAG once (`make etl-drain`) and leave it: it ends when every model says
`0 pending`, stops with an error when a run fails (the log has the end of dbt's output), and gives up after `max_iterations`.

It holds the single-writer `ducklake` pool for its whole duration, so ingests and the other dbt DAGs wait until it is done.
Manual only: in steady state the asset-triggered DAGs (`firms_dbt_sp`, `firms_dbt_nrt`, `firms_dbt_harmonize`) keep up on their own.
"""

# Airflow only parses files that mention both "airflow" and "dag" (DAG discovery safe mode).

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import DBT_EXECUTABLE, DBT_PROJECT_PATH, LAKE_WRITER_POOL


@dag(
    dag_id="firms_dbt_drain",
    schedule=None,  # manual only
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    params={"max_iterations": Param(150, type="integer", minimum=1, maximum=1000, description="Most `dbt build` runs before giving up")},
    default_args={"owner": "etl"},
    doc_md=__doc__,
    tags=["firms", "dbt", "drain"],
)
def firms_dbt_drain():
    @task(pool=LAKE_WRITER_POOL, execution_timeout=timedelta(hours=24))
    def drain(params=None):
        from include.firms.dbt_drain import drain as run

        runs = run(DBT_EXECUTABLE, DBT_PROJECT_PATH, DBT_PROJECT_PATH / "profiles.yml", int(params["max_iterations"]))
        print(f"Drained in {runs} run(s).")

    drain()


firms_dbt_drain()
