-- The CANONICAL ENTITY: one row per resolution-6 hexagon that ever had a
-- detection. The H3 id is fixed (the same id is always the same place); this
-- table adds what H3 doesn't know: real area, parents for roll-ups, activity and
-- whether it holds a static heat source. Totals use preferred rows only, so SP
-- and NRT of the same days are not counted twice.
{{ config(materialized='table', contract={'enforced': true}) }}

select
    h3_r6,
    h3_h3_to_string(h3_r6) as h3_id,
    h3_cell_to_latlng(h3_r6)[1] as center_lat,
    h3_cell_to_latlng(h3_r6)[2] as center_lon,
    h3_cell_area(h3_r6, 'km^2')::double as area_km2,
    h3_cell_to_boundary_wkt(h3_r6) as boundary_wkt,
    h3_cell_to_parent(h3_r6, 5) as h3_r5,
    h3_cell_to_parent(h3_r6, 4) as h3_r4,
    h3_cell_to_parent(h3_r6, 3) as h3_r3,
    min(acq_date) as first_date,
    max(acq_date) as last_date,
    coalesce(sum(fire_detections) filter (where is_preferred), 0)::bigint as fire_detections_total,
    coalesce(sum(static_detections) filter (where is_preferred), 0)::bigint as static_detections_total,
    coalesce(sum(static_detections), 0) > 0 as has_static_source
from {{ ref('fct_hexagon_daily') }}
group by h3_r6
