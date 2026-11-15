-- MODIS detections with the bow-tie duplicates merged: towards the edge of the scan the pixels overlap, so the
-- same fire shows up in two or more neighbouring pixels of one overpass (~8% of detections, inflating MODIS FRP
-- 26-29% once pixel size is corrected).
--
-- Two detections are the same fire when they are of the same satellite, day/night and (within 1 minute)
-- overpass, their centres are within 6 km, and they are no more than half a pixel apart BOTH along track and
-- along scan. The direction of the track comes from the latitude (the orbit inclination is 98.2 deg; Aqua day and
-- Terra night go north, Aqua night and Terra day south). Chains of duplicates are merged into one row:
-- latitude, longitude, FRP, scan and track are the mean of the members (the hexagon is the one of a member) (as in the research scripts,
-- data_exploration/k_cell/ and SUMMARY.md). frp_size_corrected divides the FRP by the pixel-size factor.
--
-- Input: preferred, non-static, MODIS confidence >= 30 (the confidence the harmonisation was built with).
-- Incremental by acquisition month: a pass never crosses a month except at midnight, and the merge key
-- includes the date, so a month is always recomputed whole.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month',
          partitioned_by=['acq_month'], sorted_by=['h3_r6'], tags=['sp']) }}
{% set rounds = 8 %}{# label propagation rounds: covers chains of up to 2^rounds pixels, far more than ever occur #}
{% set source_sql %}
    select detection_id, satellite, daynight, acq_date, acq_month, acq_datetime_utc, h3_r6,
           latitude, longitude, frp, scan, track, _dlt_load_id
    from {{ ref('fct_hexagon_detections') }}
    where sensor = 'modis' and is_preferred and not is_static_source
      and frp is not null and try_cast(confidence_raw as int) >= 30
{% endset %}

with src as materialized (
    select * from ({{ source_sql }})
    {% if is_incremental() and execute %}
    where {{ incremental_months_scope(source_sql, target_load_col='last_load_id') }}
    {% endif %}
),

p as materialized (
    select *,
        (epoch(acq_datetime_utc) / 60)::bigint as t,
        longitude * cos(radians(latitude)) * 111.32 as x,
        latitude * 111.19 as y,
        case when (satellite = 'Aqua') = (daynight = 'D')
             then asin(least(1, greatest(-1, cos(radians(98.2)) / cos(radians(latitude)))))
             else pi() - asin(least(1, greatest(-1, cos(radians(98.2)) / cos(radians(latitude))))) end as heading
    from src
),

-- pairs of duplicates: same pass (|dt| <= 1 min), within half a pixel along track and along scan
edges as materialized (
    select a.detection_id as u, b.detection_id as v
    from p a
    cross join (select unnest([-1, 0, 1]) as dt) o
    join p b on b.acq_date = a.acq_date and b.satellite = a.satellite and b.daynight = a.daynight
            and b.t = a.t + o.dt and a.detection_id < b.detection_id
    where sqrt((b.x - a.x) ^ 2 + (b.y - a.y) ^ 2) <= 6.0
      and abs((b.x - a.x) * sin((a.heading + b.heading) / 2) + (b.y - a.y) * cos((a.heading + b.heading) / 2))
          <= 0.5 * (a.track + b.track) / 2
      and abs((b.x - a.x) * cos((a.heading + b.heading) / 2) - (b.y - a.y) * sin((a.heading + b.heading) / 2))
          <= 0.5 * (a.scan + b.scan) / 2
),
sym as materialized (select u, v from edges union all select v, u from edges),

-- connected components by propagating the smallest id of the neighbourhood
l1 as materialized (select u as id, least(u, min(v)) as lbl from sym group by u)
{% for i in range(2, rounds + 1) %}
, l{{ i }} as materialized (
    select s.u as id, min(l{{ i - 1 }}.lbl) as lbl from sym s join l{{ i - 1 }} on l{{ i - 1 }}.id = s.v group by s.u
    union all select id, lbl from l{{ i - 1 }}
)
{% endfor %}
,
components as materialized (select id, min(lbl) as lbl from l{{ rounds }} group by id),

merged as (
    select
        coalesce(c.lbl, p.detection_id) as merged_id,
        min(p.acq_date) as acq_date,
        min(p.acq_month) as acq_month,
        min(p.acq_datetime_utc) as acq_datetime_utc,
        min(p.satellite) as satellite,
        min(p.daynight) as daynight,
        avg(p.latitude) as latitude,
        avg(p.longitude) as longitude,
        avg(p.frp) as frp,
        avg(p.scan) as scan,
        avg(p.track) as track,
        arg_min(p.h3_r6, p.detection_id) as h3_r6,  -- the cell of a member, so every row is in dim_hexagon
        count(*)::integer as n_merged,
        max(p._dlt_load_id) as last_load_id
    from p left join components c on c.id = p.detection_id
    group by 1
)

select
    merged_id, acq_date, acq_month, acq_datetime_utc, satellite, daynight,
    h3_r6,
    latitude, longitude, frp,
    frp / ({{ modis_size_factor('scan', 'track') }}) as frp_size_corrected,
    scan, track, n_merged, last_load_id
from merged
