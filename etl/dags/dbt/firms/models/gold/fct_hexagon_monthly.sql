-- Monthly series of the hexagon: hexagon x month x sensor x satellite, on the
-- continuous series (preferred rows: SP where published, NRT after it). Feeds the
-- per-hexagon time series and climatology. Satellites stay apart, as in daily.
--
-- Incremental by month, bounded (`firms_monthly_months_per_run`, default 12, oldest first), because rebuilding the whole history
-- at once does not fit in memory on the server. The daily table arrives in blocks of a week while this one is a month, and a
-- backfill is often one load, so the load id cannot say that another week of a month arrived. What says it is a fingerprint of
-- what the month holds on each side (cells and detections): a month is pending when it is missing here or its fingerprint
-- differs from what daily holds now. Nothing is stored for it, so the table keeps its columns.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month', partitioned_by=['acq_month'], sorted_by=['h3_r6'], on_schema_change='fail', contract={'enforced': true}) }}

{% set pending_months = [] %}
{% if execute %}
    {% set src %}
        select acq_month, count(distinct (h3_r6, sensor, satellite))::bigint as cells,
               coalesce(sum(fire_detections), 0)::bigint as det, coalesce(sum(static_detections), 0)::bigint as stat
        from {{ ref('fct_hexagon_daily') }} where is_preferred group by acq_month
    {% endset %}
    {% if is_incremental() %}
        {% set sql %}
            with s as ({{ src }}),
            t as (select acq_month, count(*)::bigint as cells, coalesce(sum(fire_detections), 0)::bigint as det,
                         coalesce(sum(static_detections), 0)::bigint as stat from {{ this }} group by acq_month)
            select strftime(s.acq_month, '%Y-%m-%d') from s left join t using (acq_month)
            where t.acq_month is null or s.cells <> t.cells or s.det <> t.det or s.stat <> t.stat order by 1
        {% endset %}
    {% else %}
        {% set sql %}select strftime(acq_month, '%Y-%m-%d') from ({{ src }}) order by 1{% endset %}
    {% endif %}
    {% set all_pending = run_query(sql).columns[0].values() %}
    {% set taken = all_pending[:var('firms_monthly_months_per_run', 12)] %}
    {% for m in taken %}{% do pending_months.append("date '" ~ m ~ "'") %}{% endfor %}
    {% do log(this ~ ": " ~ (all_pending | length) ~ " pending months, this run: " ~ (taken | join(", ") or "none"), info=true) %}
{% endif %}

with daily as (
    select * from {{ ref('fct_hexagon_daily') }}
    where is_preferred
    {% if execute %}and acq_month in ({{ pending_months | join(', ') if pending_months else "null::date" }}){% endif %}
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
