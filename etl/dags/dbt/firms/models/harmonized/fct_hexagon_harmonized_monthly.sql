-- ONE continuous monthly series per hexagon across the MODIS -> VIIRS change, in MODIS units.
--
--   before 2012-02   MODIS (Terra + Aqua), bow-tie duplicates merged, FRP size-corrected   (hrm_modis_merged)
--   from 2012-02     VIIRS S-NPP divided by k, where k = VIIRS / MODIS of the cell                (seed k_cell)
--
-- k is frozen in the seed `k_cell` (looked up per hexagon in hrm_cell_k) (hierarchical shrinkage global -> r2 -> ... -> r6, chosen by year-parity
-- cross-validation; see seeds/k_cell.csv and data_exploration/k_cell/). A cell takes the k of its own row when
-- it has one, otherwise the nearest ancestor, otherwise the global k; `k_res` says which (6 = its own, 0 = global).
-- The MODIS and VIIRS sides are kept raw as well (m_*, v_*) so overlap months can be compared.
--
-- Only S-NPP is used on the VIIRS side because k was calibrated on it and it spans 2012 onward without gaps:
-- NOAA-20 / NOAA-21 would add passes the splice has no matching MODIS for. They stay in fct_hexagon_monthly.
-- No row means "no detection in that month", not "no fire" (same as the other facts). Months before
-- 2002-07 have Terra only (m_satellites = 1): count and FRP are lower for that reason.
{{ config(materialized='table', sorted_by=['h3_r6'], tags=['harmonized']) }}
{% set splice_month = "date '2012-02-01'" %}

with modis as (
    select h3_r6, acq_month, count(*)::bigint as m_detections, sum(frp_size_corrected) as m_frp_sum,
           count(distinct satellite)::integer as m_satellites
    from {{ ref('hrm_modis_merged') }}
    group by h3_r6, acq_month
),

viirs as (
    select h3_r6, acq_month, count(*)::bigint as v_detections, sum(frp) as v_frp_sum
    from {{ ref('fct_hexagon_detections') }}
    where satellite = 'S-NPP' and is_preferred and not is_static_source
      and confidence_level <> 'low' and frp is not null
    group by h3_r6, acq_month
),

months as (
    select coalesce(m.h3_r6, v.h3_r6) as h3_r6, coalesce(m.acq_month, v.acq_month) as acq_month,
           m.m_detections, m.m_frp_sum, m.m_satellites, v.v_detections, v.v_frp_sum
    from modis m full outer join viirs v using (h3_r6, acq_month)
),

k as (select * from {{ ref('hrm_cell_k') }})

select
    m.h3_r6,
    m.acq_month,
    case when m.acq_month < {{ splice_month }} then 'modis' else 'viirs' end as harmonized_source,
    case when m.acq_month < {{ splice_month }} then coalesce(m.m_detections, 0)
         else coalesce(m.v_detections, 0) / k.k_count end::double as detections_harmonized,
    case when m.acq_month < {{ splice_month }} then coalesce(m.m_frp_sum, 0)
         else coalesce(m.v_frp_sum, 0) / k.k_frp end::double as frp_harmonized,
    m.m_detections, m.m_frp_sum, m.m_satellites, m.v_detections, m.v_frp_sum,
    k.k_count, k.k_frp, k.k_res
from months m
join k using (h3_r6)
