"""
## FIRMS dbt — SP

Runs the dbt models tagged `sp` and everything downstream (`tag:sp+`) whenever
`firms_ingest_sp` updates the `firms_raw_sp` asset:

`stg_firms__*_sp` (silver views) → `int_detections_sp` (silver) → gold
(`fct_hexagon_detections`, `fct_hexagon_daily`, `dim_hexagon`) and the static-source mask (`int_static_sources`).

One Cosmos task per model and test; every task writes through the single-writer
`ducklake` pool.
"""

# Airflow only parses files that mention both "airflow" and "dag" (DAG discovery
# safe mode). This file builds its DAG through Cosmos, so this comment keeps it visible.

import pendulum
from cosmos import DbtDag

from include.firms.config import FIRMS_RAW_SP
from include.firms.dbt import execution_config, operator_args, profile_config, project_config, render_config

firms_dbt_sp = DbtDag(
    dag_id="firms_dbt_sp",
    schedule=[FIRMS_RAW_SP],
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    project_config=project_config,
    profile_config=profile_config,
    execution_config=execution_config,
    render_config=render_config(["tag:sp+"]),  # its branch + everything downstream (gold)
    operator_args=dict(operator_args),
    default_args={"owner": "etl", "retries": 1},
    doc_md=__doc__,
    tags=["firms", "sp", "dbt"],
)
