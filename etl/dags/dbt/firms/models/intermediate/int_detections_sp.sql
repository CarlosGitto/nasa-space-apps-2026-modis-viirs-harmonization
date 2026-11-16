-- Every SP detection (MODIS + the three VIIRS satellites) in one table, latest
-- version of each detection_id. Origin columns (sensor, satellite, source_table,
-- version) tell where each row came from. Invalid rows stay, flagged in dq_issue.
-- Incremental in blocks of var('firms_block_days') days (a week), at most var('firms_blocks_per_run') per run, oldest first
-- (pending_blocks_scope): a backlog is worked through in steps that fit in memory. Deletes by day (unique_key acq_date).
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_date', partitioned_by=['acq_month'], tags=['sp']) }}
{% set source_sql = union_relations_sql(['stg_firms__modis_sp', 'stg_firms__viirs_snpp_sp', 'stg_firms__viirs_noaa20_sp', 'stg_firms__viirs_noaa21_sp']) %}

with source as (
    select * from ({{ source_sql }})
    {% if execute %}
    where {{ pending_blocks_scope(source_sql, 'acq_date', var('firms_block_days', 7), var('firms_blocks_per_run', 1), undated=True) }}
    {% endif %}
)

select *
from source
qualify row_number() over (partition by detection_id order by _dlt_load_id::double desc) = 1
