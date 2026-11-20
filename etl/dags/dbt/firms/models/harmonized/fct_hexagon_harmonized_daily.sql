-- The map layer for comparing MODIS and VIIRS on equal terms: hexagon x day x day/night x sensor, both in MODIS units.
--
--   modis   bow-tie duplicates merged, FRP size-corrected                              (hrm_modis_merged)
--   viirs   S-NPP only, divided by the hexagon's k (detections by k_count, FRP by k_frp)   (hrm_cell_k)
--
-- Same columns as fct_hexagon_daily where they matter (the API reads both the same way), but the counts are not
-- integers any more: a VIIRS detection stands for 1 / k_count MODIS-sized detections. The raw numbers
-- are kept (detections_raw, frp_raw) next to the harmonized ones. Static sources are excluded, as in the monthly series.
--
-- Incremental by block of days (`firms_block_days`, `firms_blocks_per_run`; oldest first), because rebuilding the whole history
-- on every run does not fit in memory on the server. A block is pending when it is missing here or when the rows it was built
-- from are not the ones it holds now: detections_raw (sum) against the number of source rows of the block. Nothing extra is
-- stored. The k of a hexagon (hrm_cell_k) is not part of that check: if k is refitted, rebuild with --full-refresh
-- (a block per run, repeat until nothing is pending).
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_date', partitioned_by=['acq_month'], sorted_by=['h3_r6'], on_schema_change='fail', tags=['harmonized']) }}

{% set bd = var('firms_block_days', 7) %}
{% set nb = var('firms_blocks_per_run', 1) %}
{% set blk = block_start_sql('acq_date', bd) %}
{% set scope = '1 = 0' %}
{% if execute %}
    {% set src_sql %}
        select {{ blk }} as b, sum(n)::bigint as n from (
            select acq_date, count(*) as n from {{ ref('hrm_modis_merged') }} where acq_date is not null group by acq_date
            union all
            select v.acq_date, count(*) from {{ ref('fct_hexagon_detections') }} v join {{ ref('hrm_cell_k') }} k using (h3_r6)
            where v.satellite = 'S-NPP' and v.is_preferred and not v.is_static_source
              and v.confidence_level <> 'low' and v.frp is not null and v.acq_date is not null
            group by v.acq_date
        ) group by 1
    {% endset %}
    {% if is_incremental() %}
        {% set pending_sql %}
            with s as ({{ src_sql }}),
            t as (select {{ blk }} as b, sum(detections_raw)::bigint as n from {{ this }} group by 1)
            select strftime(s.b, '%Y-%m-%d') from s left join t using (b) where t.b is null or s.n <> t.n order by 1
        {% endset %}
    {% else %}
        {% set pending_sql %}select strftime(b, '%Y-%m-%d') from ({{ src_sql }}) order by 1{% endset %}
    {% endif %}
    {% set pending = run_query(pending_sql).columns[0].values() %}
    {% set blocks = pending[:nb] %}
    {% do log(this ~ ": " ~ (pending | length) ~ " pending blocks of " ~ bd ~ " days, this run: " ~ (blocks | join(", ") or "none"), info=true) %}
    {% if blocks | length > 0 %}
        {% set in_list = [] %}
        {% for b in blocks %}{% do in_list.append("date '" ~ b ~ "'") %}{% endfor %}
        {% set scope = "acq_date >= date '" ~ blocks[0] ~ "' and acq_date < date '" ~ blocks[-1] ~ "' + interval " ~ bd ~ " day and " ~ blk ~ " in (" ~ in_list | join(", ") ~ ")" %}
    {% endif %}
{% endif %}

with modis as (
    select h3_r6, acq_date, acq_month, daynight, count(*)::bigint as detections_raw, sum(frp)::double as frp_raw,
           count(*)::double as fire_detections, sum(frp_size_corrected)::double as fire_frp_sum,
           max(frp_size_corrected)::double as fire_frp_max
    from {{ ref('hrm_modis_merged') }}
    where {{ scope }}
    group by h3_r6, acq_date, acq_month, daynight
),

viirs as (
    select h3_r6, acq_date, acq_month, daynight, count(*)::bigint as detections_raw, sum(frp)::double as frp_raw,
           max(frp)::double as frp_max_raw
    from {{ ref('fct_hexagon_detections') }}
    where satellite = 'S-NPP' and is_preferred and not is_static_source
      and confidence_level <> 'low' and frp is not null and {{ scope }}
    group by h3_r6, acq_date, acq_month, daynight
)

select h3_r6, acq_date, acq_month, 'best' as product, 'modis' as sensor, daynight, true as is_preferred,
       fire_detections, fire_frp_sum, fire_frp_max, 0::bigint as static_detections, detections_raw, frp_raw
from modis
union all
select v.h3_r6, v.acq_date, v.acq_month, 'best', 'viirs', v.daynight, true,
       v.detections_raw / k.k_count, v.frp_raw / k.k_frp, v.frp_max_raw / k.k_frp, 0::bigint, v.detections_raw, v.frp_raw
from viirs v join {{ ref('hrm_cell_k') }} k using (h3_r6)
