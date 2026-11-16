"""
## FIRMS dbt — harmonization

Builds the harmonized MODIS + VIIRS data (`tag:harmonized`) whenever `firms_dbt_sp` or `firms_dbt_nrt` finish
(they update the `firms_gold` asset), on top of the fresh silver and gold:

- `k_cell` (seed): VIIRS / MODIS factor per hexagon, frozen. `hrm_cell_k` looks it up (own cell, ancestor, global).
- `hrm_modis_merged`: MODIS with the bow-tie duplicates merged and the FRP corrected for pixel size.
- `fct_hexagon_harmonized_daily` / `fct_hexagon_harmonized_monthly`: MODIS and VIIRS in the same units (MODIS units),
  and the one series across the 2012 change. They feed the Compare view, the burn calendar and the hexagon report.
- `fct_area_coverage_monthly`: which area-months were actually downloaded (so a gap is not read as "no fire").
- `reliability_model` (seed), `hrm_detection_reliability`, `fct_hexagon_reliability_monthly`: persistence and the
  reliability index of each detection and hexagon.

Incremental where the model is (`hrm_modis_merged`, `hrm_detection_reliability`: only the months touched by new loads);
the rest are rebuilt (seconds). One Cosmos task per model, seed and test; all write through the single-writer
`ducklake` pool. Also runs in `firms_dbt_reprocess` (full refresh); a huge first build of `hrm_detection_reliability` is
better done by month (see the model).
"""

# Airflow only parses files that mention both "airflow" and "dag" (DAG discovery
# safe mode). This file builds its DAG through Cosmos, so this comment keeps it visible.

import pendulum
from cosmos import DbtDag

from include.firms.config import FIRMS_GOLD
from include.firms.dbt import execution_config, operator_args, profile_config, project_config, render_config

firms_dbt_harmonize = DbtDag(
    dag_id="firms_dbt_harmonize",
    schedule=[FIRMS_GOLD],
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    project_config=project_config,
    profile_config=profile_config,
    execution_config=execution_config,
    render_config=render_config(["tag:harmonized"]),  # these models and seeds only; silver and gold are already built
    operator_args=dict(operator_args),
    default_args={"owner": "etl", "retries": 1},
    doc_md=__doc__,
    tags=["firms", "dbt", "harmonize"],
)
