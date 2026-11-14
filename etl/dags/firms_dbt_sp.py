"""
## FIRMS dbt — Standard Processing

Runs the dbt models tagged `sp` (`silver.*_sp` → `gold.*_sp`) whenever
`firms_ingest_sp` updates the `firms_raw_sp` asset. One Cosmos task per
model and test; MODIS and VIIRS stay on separate paths.
"""

# Airflow only parses files that mention both "airflow" and "dag" (DAG discovery
# safe mode). This file builds its DAG through Cosmos, so this comment keeps it visible.

import pendulum
from cosmos import DbtDag, RenderConfig

from include.firms.config import FIRMS_RAW_SP
from include.firms.dbt import execution_config, operator_args, profile_config, project_config

firms_dbt_sp = DbtDag(
    dag_id="firms_dbt_sp",
    schedule=[FIRMS_RAW_SP],
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    project_config=project_config,
    profile_config=profile_config,
    execution_config=execution_config,
    render_config=RenderConfig(select=["tag:sp"]),
    operator_args=dict(operator_args),
    default_args={"owner": "etl", "retries": 1},
    doc_md=__doc__,
    tags=["firms", "sp", "dbt"],
)
