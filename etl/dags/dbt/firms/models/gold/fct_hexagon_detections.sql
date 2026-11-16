-- DETAIL of the hexagon: one row per valid detection, SP and NRT together, keyed
-- to its resolution-6 hexagon (h3_r6). Origin columns say where it came from.
--
--   is_static_source  not a wildfire: SP `type` 1/2/3, or (NRT, which has no type)
--                     inside the static-source mask built from SP
--   is_preferred      the best available version for the series: SP always; NRT
--                     only after the last SP date of the same satellite. Filter
--                     on it to get one continuous series without duplicates.
--
-- Incremental in blocks of var('firms_block_days') days (a week) over both intermediate tables, at most var('firms_blocks_per_run')
-- per run, oldest first (pending_blocks_scope): when new SP days land, their blocks are recomputed and the NRT rows of
-- those days stop being preferred. Deletes by day (unique_key acq_date).
-- (A static source discovered later only reaches older NRT days on a full refresh.)
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_date', partitioned_by=['acq_month'], sorted_by=['h3_r6']) }}
{% set source_sql %}
    select * from {{ ref('int_detections_sp') }} where dq_issue is null
    union all by name
    select * from {{ ref('int_detections_nrt') }} where dq_issue is null
{% endset %}

with detections as (
    select * exclude (dq_issue) from ({{ source_sql }})
    {% if execute %}
    where {{ pending_blocks_scope(source_sql, 'acq_date', var('firms_block_days', 7), var('firms_blocks_per_run', 1)) }}
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
