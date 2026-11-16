-- Monthly series of the hexagon: hexagon x month x sensor x satellite, on the
-- continuous series (preferred rows: SP where published, NRT after it). Feeds the
-- per-hexagon time series and climatology. Satellites stay apart, as in daily.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month',
          partitioned_by=['acq_month'], sorted_by=['h3_r6'], on_schema_change='fail', contract={'enforced': true}) }}
{% set source_sql %}
    select * from {{ ref('fct_hexagon_daily') }} where is_preferred
{% endset %}

with daily as (
    select * from ({{ source_sql }})
    {% if execute %}
    where {{ pending_months_scope(source_sql, 'acq_date', var('firms_months_per_run', 1), source_load_col='last_load_id', target_load_col='last_load_id', month_col='acq_month') }}
    {% endif %}
)

select
    h3_r6,
    acq_month,
    sensor,
    satellite,
    string_agg(distinct product, ',' order by product) as products,  -- sp, nrt or both
    sum(fire_detections)::bigint as fire_detections,
    coalesce(sum(fire_detections) filter (where daynight = 'D'), 0)::bigint as fire_day,
    coalesce(sum(fire_detections) filter (where daynight = 'N'), 0)::bigint as fire_night,
    sum(fire_frp_sum)::double as fire_frp_sum,
    max(fire_frp_max)::double as fire_frp_max,
    sum(fire_high_conf)::bigint as fire_high_conf,
    count(distinct acq_date) filter (where fire_detections > 0)::bigint as active_days,
    sum(static_detections)::bigint as static_detections,
    max(last_load_id) as last_load_id
from daily
group by h3_r6, acq_month, sensor, satellite
