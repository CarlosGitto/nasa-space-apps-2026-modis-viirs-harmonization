-- NASA POWER daily weather per grid cell and UTC day, latest version of each (a day
-- reloaded keeps its newest load). Invalid rows stay, flagged in dq_issue.
-- Incremental: only the months with new loads are recomputed, at most
-- var('weather_months_per_run') per run (pending_months_scope): a backlog is worked
-- through in steps that fit in memory, the rest stays pending.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='obs_month',
          partitioned_by=['obs_month'], sorted_by=['cell_lat', 'cell_lon'], tags=['weather'],
          on_schema_change='sync_all_columns') }}
{% set source_sql %}
    select * from {{ ref('stg_weather__power_daily') }}
{% endset %}
{% set value_columns = ['t2m_max_c', 'precipitation_mm', 'wind_speed_mean_ms', 'wind_dir_mean_deg',
                        'dq_issue', '_dlt_load_id'] %}

with source as (
    select cell_lat, cell_lon, obs_date, obs_month, {{ value_columns | join(', ') }}
    from ({{ source_sql }})
    where {{ pending_months_scope(source_sql, 'obs_date', var('weather_months_per_run', 6)) }}
),

latest as (
    select
        cell_lat, cell_lon, obs_date, obs_month,
        arg_max({ {%- for c in value_columns %}'{{ c }}': {{ c }}{{ ', ' if not loop.last }}{% endfor -%} }, _dlt_load_id) as v
    from source
    group by cell_lat, cell_lon, obs_date, obs_month
)

select cell_lat, cell_lon, obs_date, obs_month,
       {% for c in value_columns %}v.{{ c }}{{ ', ' if not loop.last }}{% endfor %}
from latest
