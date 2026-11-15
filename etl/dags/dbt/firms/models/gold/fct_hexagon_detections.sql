-- DETAIL of the hexagon: one row per valid detection, SP and NRT together, keyed
-- to its resolution-6 hexagon (h3_r6). Origin columns say where it came from.
--
--   is_static_source  not a wildfire: SP `type` 1/2/3, or (NRT, which has no type)
--                     inside the static-source mask built from SP
--   is_preferred      the best available version for the series: SP always; NRT
--                     only after the last SP date of the same satellite. Filter
--                     on it to get one continuous series without duplicates.
--
-- Incremental by acquisition month over both intermediate tables: when a new SP
-- month lands, that month is recomputed and its NRT rows stop being preferred.
-- (A static source discovered later only reaches older NRT months on a full refresh.)
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month', partitioned_by=['acq_month']) }}
{% set source_sql %}
    select * from {{ ref('int_detections_sp') }} where dq_issue is null
    union all by name
    select * from {{ ref('int_detections_nrt') }} where dq_issue is null
{% endset %}

with detections as (
    select * exclude (dq_issue) from ({{ source_sql }})
    {% if is_incremental() and execute %}
    where {{ incremental_months_scope(source_sql) }}
    {% endif %}
),

sp_coverage as (
    select satellite, max(acq_date) as last_sp_date
    from {{ ref('int_detections_sp') }}
    where dq_issue is null
    group by satellite
),

static_mask as (
    select h3_r8 from {{ ref('int_static_sources') }}
)

select
    d.*,
    case
        when d.product = 'sp' then coalesce(d.fire_type in (1, 2, 3), false)
        else d.h3_r8 in (select h3_r8 from static_mask)
    end as is_static_source,
    case
        when d.product = 'sp' then true
        else d.acq_date > coalesce(c.last_sp_date, date '1900-01-01')
    end as is_preferred
from detections d
left join sp_coverage c using (satellite)
