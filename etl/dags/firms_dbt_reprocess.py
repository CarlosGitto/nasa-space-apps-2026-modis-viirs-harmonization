"""
## FIRMS dbt — reprocess

Rebuilds every dbt model from the raw data **already in the lake**, without
downloading anything from FIRMS or NASA POWER: `dbt build --full-refresh`, model
by model in dependency order, so each change cascades downstream:

bronze `firms_raw` (untouched) → silver (`stg_firms__*`, `int_detections_*`,
`int_static_sources`) → gold (`fct_hexagon_*`, `dim_hexagon`); and likewise
bronze `weather_raw` → `stg_weather__power_daily`, `int_weather_power_daily` →
`fct_weather_daily`.

Use it after changing a model or a macro (new column, new rule, a fix), or when
silver/gold look inconsistent. Manual only (`make etl-reprocess` or ▶ in the UI);
the scheduled SP/NRT dbt DAGs stay incremental.

One Cosmos task per model and test; every task writes through the single-writer
`ducklake` pool, so it can't clash with the ingest or the scheduled dbt runs.
"""

# Airflow only parses files that mention both "airflow" and "dag" (DAG discovery
# safe mode). This file builds its DAG through Cosmos, so this comment keeps it visible.

import pendulum
from cosmos import DbtDag

from include.firms.dbt import execution_config, operator_args, profile_config, project_config, render_config

firms_dbt_reprocess = DbtDag(
    dag_id="firms_dbt_reprocess",
    schedule=None,  # manual only
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    project_config=project_config,
    profile_config=profile_config,
    execution_config=execution_config,
    render_config=render_config(),  # every model: silver and gold
    # Incremental models rebuilt from scratch (Cosmos ignores it on test tasks).
    operator_args={**operator_args, "full_refresh": True},
    default_args={"owner": "etl", "retries": 1},
    doc_md=__doc__,
    tags=["firms", "dbt", "reprocess"],
)
