"""
## Weather dbt

Runs the dbt models tagged `weather` and everything downstream (`tag:weather+`)
whenever `weather_ingest` updates the `weather_raw` asset:

`stg_weather__*` (silver views: units, relative humidity, VPD, wind speed and
direction) → `int_weather_merra2_hourly`, `int_weather_imerg_daily` (silver:
latest version, IMERG Final preferred over Late) → gold
(`fct_weather_daily`, `fct_precipitation_daily`).

Weather gold is keyed by grid cell and day, independent of the fire models:
the backend looks up the cell under a hexagon. One Cosmos task per model and
test; every task writes through the single-writer `ducklake` pool.

**Bounded runs.** The big weather models recompute at most
`weather_months_per_run` months per run (dbt var, default 2), oldest pending
first (macros/incremental.sql, pending_months_scope): a backlog (a long backfill,
or dbt failing for a while) is worked through in steps that fit in DuckDB's
memory cap instead of one query over every month. Besides each weather load, the
DAG also runs every hour so a backlog keeps draining when no loads come in; with
nothing pending those runs are no-ops.
"""

# Airflow only parses files that mention both "airflow" and "dag" (DAG discovery
# safe mode). This file builds its DAG through Cosmos, so this comment keeps it visible.

import pendulum
from airflow.timetables.assets import AssetOrTimeSchedule
from airflow.timetables.trigger import CronTriggerTimetable
from cosmos import DbtDag

from include.firms.dbt import execution_config, operator_args, profile_config, project_config, render_config
from include.weather.config import WEATHER_RAW

weather_dbt = DbtDag(
    dag_id="weather_dbt",
    # After every weather load, and hourly to drain a backlog (bounded runs, see above).
    schedule=AssetOrTimeSchedule(timetable=CronTriggerTimetable("0 * * * *", timezone="UTC"), assets=[WEATHER_RAW]),
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    project_config=project_config,
    profile_config=profile_config,
    execution_config=execution_config,
    render_config=render_config(["tag:weather+"]),
    operator_args=dict(operator_args),
    default_args={"owner": "etl", "retries": 1},
    doc_md=__doc__,
    tags=["weather", "dbt"],
)
