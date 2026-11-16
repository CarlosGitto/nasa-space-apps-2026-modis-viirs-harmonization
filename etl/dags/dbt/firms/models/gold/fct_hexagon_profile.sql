-- PROFILE of the hexagon ("ficha técnica", whole history): one row per hexagon x
-- sensor with the answers a user asks first, precomputed so the API only reads
-- one row. Built on the continuous series (preferred rows). Sensors are never
-- added together (MODIS and VIIRS see fire differently); satellites of one sensor
-- are, so counts are "detections", not "fires".
--
-- Recent activity (last_7d / last_30d) is relative to data_end_date, the last day
-- with data in the whole lake, not to today.
{{ config(materialized='table', sorted_by=['h3_r6'], contract={'enforced': true}) }}

with daily as (
    select * from {{ ref('fct_hexagon_daily') }} where is_preferred
),

data_end as (
    select max(acq_date) as data_end_date from daily
),

per_day as (
    select h3_r6, sensor, acq_date, sum(fire_detections) as fire
    from daily group by all
),

per_month_of_year as (
    select h3_r6, sensor, month(acq_date) as month_of_year, sum(fire_detections) as fire
    from daily group by all
),

totals as (
    select
        h3_r6,
        sensor,
        min(acq_date) filter (where fire_detections > 0) as first_date,
        max(acq_date) filter (where fire_detections > 0) as last_date,
        count(distinct acq_date) filter (where fire_detections > 0) as active_days,
        sum(fire_detections) as fire_detections,
        sum(fire_frp_sum) as fire_frp_sum,
        max(fire_frp_max) as fire_frp_max,
        sum(fire_high_conf) as fire_high_conf,
        sum(fire_detections) filter (where daynight = 'D') as fire_day,
        sum(static_detections) as static_detections,
        count(distinct satellite) as satellites,
        sum(fire_detections) filter (where acq_date > (select data_end_date from data_end) - 7) as last_7d,
        sum(fire_detections) filter (where acq_date > (select data_end_date from data_end) - 30) as last_30d
    from daily
    group by h3_r6, sensor
),

peak_day as (
    select h3_r6, sensor, arg_max(acq_date, fire) as peak_date, max(fire) as peak_day_detections
    from per_day where fire > 0 group by all
),

climatology as (
    select
        h3_r6,
        sensor,
        [{% for m in range(1, 13) %}coalesce(sum(fire) filter (where month_of_year = {{ m }}), 0)::bigint{% if not loop.last %}, {% endif %}{% endfor %}] as climatology,
        arg_max(month_of_year, fire) filter (where fire > 0) as peak_month
    from per_month_of_year group by all
),

frp_p90 as (
    select h3_r6, sensor, quantile_cont(frp, 0.9) as fire_frp_p90
    from {{ ref('fct_hexagon_detections') }}
    where is_preferred and not is_static_source
    group by all
)

select
    t.h3_r6,
    t.sensor,
    t.first_date,
    t.last_date,
    coalesce(t.active_days, 0)::bigint as active_days,
    coalesce(t.fire_detections, 0)::bigint as fire_detections,
    coalesce(t.fire_frp_sum, 0)::double as fire_frp_sum,
    t.fire_frp_max::double as fire_frp_max,
    (t.fire_frp_sum / nullif(t.fire_detections, 0))::double as fire_frp_mean,
    p.fire_frp_p90::double as fire_frp_p90,
    (t.fire_high_conf / nullif(t.fire_detections, 0))::double as high_conf_share,
    (coalesce(t.fire_day, 0) / nullif(t.fire_detections, 0))::double as day_share,
    d.peak_date,
    d.peak_day_detections::bigint as peak_day_detections,
    c.peak_month::integer as peak_month,
    c.climatology,
    coalesce(t.last_7d, 0)::bigint as last_7d,
    coalesce(t.last_30d, 0)::bigint as last_30d,
    (select data_end_date from data_end) as data_end_date,
    coalesce(t.static_detections, 0)::bigint as static_detections,
    (t.static_detections / nullif(t.static_detections + t.fire_detections, 0))::double as static_share,
    t.satellites::bigint as satellites
from totals t
left join peak_day d using (h3_r6, sensor)
left join climatology c using (h3_r6, sensor)
left join frp_p90 p using (h3_r6, sensor)
