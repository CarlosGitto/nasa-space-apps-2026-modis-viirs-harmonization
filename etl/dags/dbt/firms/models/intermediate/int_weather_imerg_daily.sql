-- GPM IMERG daily precipitation per grid cell, one version per cell and day: Final
-- (research grade) when NASA has published it, else Late (near real time); within a
-- run, the latest load. imerg_run says which one. Invalid rows stay, flagged in dq_issue.
-- Incremental: only the months touched by new loads are recomputed, so a month whose
-- Final arrives later switches from Late to Final on the next run.
--
-- ~330 k cells per day over South America: deduplicated with a grouped arg_max, which
-- DuckDB can spill to disk, instead of a window over every row. The per-file lineage
-- (_granule, _collection, _area, _extracted_at) stays in bronze, joinable by _dlt_load_id.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='obs_month',
          partitioned_by=['obs_month'], sorted_by=['cell_lat', 'cell_lon'], tags=['weather']) }}
{% set source_sql = union_relations_sql(['stg_weather__imerg_final_daily', 'stg_weather__imerg_late_daily']) %}

with source as (
    select cell_lat, cell_lon, obs_date, obs_month, imerg_run, precipitation_mm, dq_issue, _dlt_load_id
    from ({{ source_sql }})
    {% if is_incremental() and execute %}
    where {{ incremental_months_scope(source_sql, date_col='obs_date') }}
    {% endif %}
),

latest as (
    select
        cell_lat, cell_lon, obs_date, obs_month,
        arg_max(
            {'imerg_run': imerg_run, 'precipitation_mm': precipitation_mm, 'dq_issue': dq_issue, '_dlt_load_id': _dlt_load_id},
            {'is_final': imerg_run = 'final', 'load': _dlt_load_id}  -- Final first, then the latest load
        ) as v
    from source
    group by cell_lat, cell_lon, obs_date, obs_month
)

select cell_lat, cell_lon, obs_date, obs_month,
       v.imerg_run, v.precipitation_mm, v.dq_issue, v._dlt_load_id
from latest
