-- Persistence and reliability of every detection (wildfire, preferred row), the composite index of SUMMARY.md stage 14.
--
--   n_pairs      graded persistence: detections of the same sensor family within 1.5 km that are 1-36 h away
--                (another overpass, not the same one). MODIS Terra + Aqua form one family; every VIIRS satellite is its own.
--   persistent   n_pairs > 0 (the persistent-fire flag)
--   reliability  P(an independent sensor confirms the detection): logistic model on confidence, ln(1 + n_pairs) and
--                ln(1 + FRP), one set of coefficients per sensor, frozen in the seed reliability_model
--                (fitted by data_exploration/reliability/reliability_fit.py on the 2019 windows; out-of-fold AUC
--                0.66 MODIS / 0.73 VIIRS). It measures how likely the detection is to be confirmed, not truth.
--
-- Neighbours are looked up on H3 res-7 cells (a ring of 1 covers 1.5 km) +- 2 days. Incremental by month, at most
-- var('firms_months_per_run') per run (pending_months_scope), oldest first. A detection near the end of a month has neighbours in
-- the first 2 days of the next one, so a month also becomes pending when those days change, and it stores that load as its own
-- (last_load_id): built before its successor existed, it is rebuilt once the successor lands, and then it is up to date.
-- A specific set of months can be forced with
--   dbt build -s hrm_detection_reliability --vars '{only_months: ["2019-01-01", "2019-02-01"]}'
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month',
          partitioned_by=['acq_month'], sorted_by=['h3_r6'], tags=['harmonized']) }}
{% set base_sql %}
    select detection_id, sensor, satellite, acq_date, acq_month, acq_datetime_utc, h3_r6, latitude, longitude, frp,
           confidence_raw, _dlt_load_id,
           case when sensor = 'modis' then 'modis' else satellite end as family
    from {{ ref('fct_hexagon_detections') }}
    where is_preferred and not is_static_source and frp is not null
{% endset %}
{% set only_months = var('only_months', none) %}
{# Rows for the pending check: every detection, plus the first 2 days of each month once more as if they were the last 2 of the month
   before, only when that month has detections of its own (otherwise it would stay pending for ever: there is nothing to build). #}
{% set pending_sql %}
    select acq_date, _dlt_load_id from ({{ base_sql }})
    union all
    select (acq_date - 2)::date as acq_date, _dlt_load_id from ({{ base_sql }}) where day(acq_date) <= 2
      and date_trunc('month', acq_date - 2) in (select date_trunc('month', acq_date) from ({{ base_sql }}) group by 1)
{% endset %}

with src as materialized (
    select * from ({{ base_sql }})
    {% if only_months %}
    where acq_month in ({% for m in only_months %}date '{{ m }}'{% if not loop.last %}, {% endif %}{% endfor %})
    {% elif execute %}
    where {{ pending_months_scope(pending_sql, 'acq_date', var('firms_months_per_run', 1), target_load_col='last_load_id', month_col='acq_month') }}
    {% endif %}
),

a as materialized (
    select *, (epoch(acq_datetime_utc) / 60)::bigint as t, h3_latlng_to_cell(latitude, longitude, 7) as c7 from src
),

pool as materialized (
    select family, acq_date, (epoch(acq_datetime_utc) / 60)::bigint as t, latitude, longitude,
           h3_latlng_to_cell(latitude, longitude, 7) as c7, _dlt_load_id
    from ({{ base_sql }})
    where acq_date between (select min(acq_date) from src) - 2 and (select max(acq_date) from src) + 2
),

pairs as materialized (
    select a.detection_id, count(*)::integer as n_pairs
    from a
    cross join unnest(h3_grid_disk(a.c7, 1)) as ring(c7)
    cross join (select unnest([-2, -1, 0, 1, 2]) as d) days
    -- the day is part of the join key (36 h reach at most 2 calendar days): with a range filter instead, building a whole
    -- year at once compared every detection with the whole year of its neighbouring cells (~70x more pairs, same result)
    join pool b on b.family = a.family and b.c7 = ring.c7 and b.acq_date = a.acq_date + days.d
    where abs(b.t - a.t) between 60 and 2160
      and sqrt(((b.longitude - a.longitude) * cos(radians((a.latitude + b.latitude) / 2)) * 111.32) ^ 2
               + ((b.latitude - a.latitude) * 111.19) ^ 2) <= 1.5
    group by a.detection_id
),

-- newest load among the first 2 days of the month AFTER each month: part of what this month's pairs depend on
next_month as (
    select date_trunc('month', acq_date - 2)::date as m, max(_dlt_load_id) as l
    from pool where day(acq_date) <= 2 group by 1
),

scored as (
    select a.detection_id, a.acq_date, a.acq_month, a.h3_r6, a.sensor, a.satellite, a.family, a.frp,
           coalesce(p.n_pairs, 0) as n_pairs,
           case when a.sensor = 'modis' then try_cast(a.confidence_raw as double) / 100
                else case lower(a.confidence_raw) when 'l' then 0.0 when 'n' then 0.5 when 'h' then 1.0 end end as confidence,
           greatest(a._dlt_load_id, coalesce(nm.l, a._dlt_load_id)) as _dlt_load_id
    from a left join pairs p using (detection_id)
    left join next_month nm on nm.m = a.acq_month
)

select s.detection_id, s.acq_date, s.acq_month, s.h3_r6, s.sensor, s.satellite, s.family, s.frp, s.confidence, s.n_pairs,
       s.n_pairs > 0 as persistent,
       1 / (1 + exp(-(m.intercept + m.b_conf * coalesce(s.confidence, 0.5) + m.b_pairs * ln(1 + s.n_pairs)
                       + m.b_lfrp * ln(1 + s.frp)))) as reliability,
       s._dlt_load_id as last_load_id
from scored s
join {{ ref('reliability_model') }} m on m.sensor = s.sensor
