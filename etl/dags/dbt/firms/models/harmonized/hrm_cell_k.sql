-- k (VIIRS S-NPP / MODIS) of every hexagon, from the frozen seed k_cell: the cell's own row when it has one,
-- otherwise the nearest ancestor (r5 -> r2), otherwise the global k. `k_res` is the resolution it came from
-- (6 = own, 0 = global). Shared by the monthly series and the daily map layer so both use the same k.
{{ config(materialized='table', sorted_by=['h3_r6']) }}

with cells as (
    select h3_r6, h3_h3_to_string(h3_r6) as k6, h3_h3_to_string(h3_cell_to_parent(h3_r6, 5)) as k5,
           h3_h3_to_string(h3_cell_to_parent(h3_r6, 4)) as k4, h3_h3_to_string(h3_cell_to_parent(h3_r6, 3)) as k3,
           h3_h3_to_string(h3_cell_to_parent(h3_r6, 2)) as k2
    from {{ ref('dim_hexagon') }}
)

select c.h3_r6,
       coalesce(s6.k_frp, s5.k_frp, s4.k_frp, s3.k_frp, s2.k_frp, g.k_frp) as k_frp,
       coalesce(s6.k_count, s5.k_count, s4.k_count, s3.k_count, s2.k_count, g.k_count) as k_count,
       case when s6.h3 is not null then 6 when s5.h3 is not null then 5 when s4.h3 is not null then 4
            when s3.h3 is not null then 3 when s2.h3 is not null then 2 else 0 end::integer as k_res
from cells c
left join {{ ref('k_cell') }} s6 on s6.h3 = c.k6 and s6.res = 6
left join {{ ref('k_cell') }} s5 on s5.h3 = c.k5 and s5.res = 5
left join {{ ref('k_cell') }} s4 on s4.h3 = c.k4 and s4.res = 4
left join {{ ref('k_cell') }} s3 on s3.h3 = c.k3 and s3.res = 3
left join {{ ref('k_cell') }} s2 on s2.h3 = c.k2 and s2.res = 2
cross join (select k_frp, k_count from {{ ref('k_cell') }} where res = 0) g
