-- The map layer for comparing MODIS and VIIRS on equal terms: hexagon x day x day/night x sensor, both in MODIS units.
--
--   modis   bow-tie duplicates merged, FRP size-corrected                              (hrm_modis_merged)
--   viirs   S-NPP only, divided by the hexagon's k (detections by k_count, FRP by k_frp)   (hrm_cell_k)
--
-- Same columns as fct_hexagon_daily where they matter (the API reads both the same way), but the counts are not
-- integers any more: a VIIRS detection stands for 1 / k_count MODIS-sized detections. The raw numbers
-- are kept (detections_raw, frp_raw) next to the harmonized ones. Static sources are excluded, as in the monthly series.
{{ config(materialized='table', partitioned_by=['acq_month'], sorted_by=['h3_r6'], tags=['harmonized']) }}

with modis as (
    select h3_r6, acq_date, acq_month, daynight, count(*)::bigint as detections_raw, sum(frp)::double as frp_raw,
           count(*)::double as fire_detections, sum(frp_size_corrected)::double as fire_frp_sum,
           max(frp_size_corrected)::double as fire_frp_max
    from {{ ref('hrm_modis_merged') }}
    group by h3_r6, acq_date, acq_month, daynight
),

viirs as (
    select h3_r6, acq_date, acq_month, daynight, count(*)::bigint as detections_raw, sum(frp)::double as frp_raw,
           max(frp)::double as frp_max_raw
    from {{ ref('fct_hexagon_detections') }}
    where satellite = 'S-NPP' and is_preferred and not is_static_source
      and confidence_level <> 'low' and frp is not null
    group by h3_r6, acq_date, acq_month, daynight
)

select h3_r6, acq_date, acq_month, 'best' as product, 'modis' as sensor, daynight, true as is_preferred,
       fire_detections, fire_frp_sum, fire_frp_max, 0::bigint as static_detections, detections_raw, frp_raw
from modis
union all
select v.h3_r6, v.acq_date, v.acq_month, 'best', 'viirs', v.daynight, true,
       v.detections_raw / k.k_count, v.frp_raw / k.k_frp, v.frp_max_raw / k.k_frp, 0::bigint, v.detections_raw, v.frp_raw
from viirs v join {{ ref('hrm_cell_k') }} k using (h3_r6)
