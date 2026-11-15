-- Static heat sources (not wildfires): oil and gas flares, refineries, industry,
-- offshore platforms and volcanoes. SP classifies them per detection (`type` 1, 2
-- or 3) but NRT doesn't, so this builds a mask NRT can be matched against.
--
-- A resolution-8 cell (~0.7 km²) is static when SP flagged non-vegetation fire
-- there on at least 2 different days. The mask also includes its neighbours
-- (h3_grid_disk k=1, ~1 km) to absorb the position jitter between overpasses.
{{ config(materialized='table', tags=['sp']) }}

with flagged as (
    select
        h3_r8,
        mode(fire_type) as fire_type,
        count(*) as detections,
        count(distinct acq_date) as days
    from {{ ref('int_detections_sp') }}
    where dq_issue is null and fire_type in (1, 2, 3) and h3_r8 is not null
    group by h3_r8
    having count(distinct acq_date) >= 2
),

expanded as (
    select unnest(h3_grid_disk(h3_r8, 1)) as h3_r8, h3_r8 as core_cell, fire_type, detections, days
    from flagged
)

select
    h3_r8,
    arg_max(fire_type, days) as fire_type,  -- 1 volcano, 2 static land source, 3 offshore
    max(days) as days,
    max(detections) as detections,
    bool_or(h3_r8 = core_cell) as is_core   -- false: neighbour added by the k=1 ring
from expanded
group by h3_r8
