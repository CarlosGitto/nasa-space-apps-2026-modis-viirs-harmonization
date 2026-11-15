-- MERRA-2 hourly weather per grid cell, latest version of each cell and hour. Invalid
-- rows stay, flagged in dq_issue. Kept hourly in silver so a later model can match the
-- weather at the exact hour of a satellite overpass.
-- Incremental: only the months touched by new loads are recomputed.
--
-- ~260 k rows per day over South America: deduplicated with a grouped arg_max, which
-- DuckDB can spill to disk, instead of a window over every row. The per-file lineage
-- (_granule, _collection, _area, _extracted_at) stays in bronze, joinable by _dlt_load_id.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='obs_month',
          partitioned_by=['obs_month'], sorted_by=['cell_lat', 'cell_lon'], tags=['weather']) }}
{% set source_sql %}
    select * from {{ ref('stg_weather__merra2_slv_hourly') }}
{% endset %}
{% set value_columns = ['t2m_c', 'dewpoint_c', 'u10_ms', 'v10_ms', 'wind_speed_ms', 'wind_dir_deg',
                        'rh_pct', 'vpd_kpa', 'dq_issue', '_dlt_load_id'] %}

with source as (
    select cell_lat, cell_lon, time_utc, obs_date, obs_month, {{ value_columns | join(', ') }}
    from ({{ source_sql }})
    {% if is_incremental() and execute %}
    where {{ incremental_months_scope(source_sql, date_col='obs_date') }}
    {% endif %}
),

latest as (
    select
        cell_lat, cell_lon, time_utc, obs_date, obs_month,
        arg_max({ {%- for c in value_columns %}'{{ c }}': {{ c }}{{ ', ' if not loop.last }}{% endfor -%} }, _dlt_load_id) as v
    from source
    group by cell_lat, cell_lon, time_utc, obs_date, obs_month
)

select cell_lat, cell_lon, time_utc, obs_date, obs_month,
       {% for c in value_columns %}v.{{ c }}{{ ', ' if not loop.last }}{% endfor %}
from latest
