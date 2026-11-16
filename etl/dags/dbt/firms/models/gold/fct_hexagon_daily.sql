-- METRICS of the hexagon: one row per hexagon × day × product × sensor × satellite
-- × day/night. Satellites are never added together (each sees fire differently),
-- and FRP is summed within one overpass only. Any time window is built per
-- request by summing days. Static heat sources are counted apart, never as fire.
--
-- Only hexagon-days with detections exist: no row means "no detection", not "no
-- fire" (no cloud/coverage mask yet).
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month', partitioned_by=['acq_month'], sorted_by=['h3_r6'], on_schema_change='fail', contract={'enforced': true}) }}
{% set source_sql %}
    select * from {{ ref('fct_hexagon_detections') }}
{% endset %}

with detections as (
    select * from ({{ source_sql }})
    {% if execute %}
    where {{ pending_months_scope(source_sql, 'acq_date', var('firms_months_per_run', 1), target_load_col='last_load_id', month_col='acq_month') }}
    {% endif %}
)

select
    h3_r6,
    acq_date,
    acq_month,
    product,
    sensor,
    satellite,
    daynight,
    bool_and(is_preferred) as is_preferred,
    count(*) filter (where not is_static_source)::bigint as fire_detections,
    (sum(frp) filter (where not is_static_source))::double as fire_frp_sum,
    (max(frp) filter (where not is_static_source))::double as fire_frp_max,
    (avg(frp) filter (where not is_static_source))::double as fire_frp_mean,
    count(*) filter (where not is_static_source and confidence_level = 'high')::bigint as fire_high_conf,
    count(*) filter (where is_static_source)::bigint as static_detections,
    (sum(frp) filter (where is_static_source))::double as static_frp_sum,
    max(_dlt_load_id) as last_load_id
from detections
group by h3_r6, acq_date, acq_month, product, sensor, satellite, daynight
