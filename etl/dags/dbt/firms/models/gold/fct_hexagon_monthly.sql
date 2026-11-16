-- Monthly series of the hexagon: hexagon x month x sensor x satellite, on the
-- continuous series (preferred rows: SP where published, NRT after it). Feeds the
-- per-hexagon time series and climatology. Satellites stay apart, as in daily.
-- Rebuilt whole on every run (a table, not incremental): it aggregates fct_hexagon_daily, which is already small, and the daily
-- table now arrives in blocks of a week while this one is a month. An incremental month could never tell that another week of
-- the month had arrived (a backfill is often one load, so the load id does not change), and would stay partial.
{{ config(materialized='table', partitioned_by=['acq_month'], sorted_by=['h3_r6'], contract={'enforced': true}) }}

with daily as (
    select * from {{ ref('fct_hexagon_daily') }} where is_preferred
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
