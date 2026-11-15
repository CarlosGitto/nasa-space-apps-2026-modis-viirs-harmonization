-- Every NRT detection (MODIS + the three VIIRS satellites) in one table, latest
-- version of each detection_id. Origin columns (sensor, satellite, source_table,
-- version) tell where each row came from. Invalid rows stay, flagged in dq_issue.
-- Incremental: only the acquisition months touched by new loads are recomputed.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='acq_month', partitioned_by=['acq_month'], tags=['nrt']) }}
{% set source_sql = union_relations_sql(['stg_firms__modis_nrt', 'stg_firms__viirs_snpp_nrt', 'stg_firms__viirs_noaa20_nrt', 'stg_firms__viirs_noaa21_nrt']) %}

with source as (
    select * from ({{ source_sql }})
    {% if is_incremental() and execute %}
    where {{ incremental_months_scope(source_sql) }}
    {% endif %}
)

select *
from source
qualify row_number() over (partition by detection_id order by _dlt_load_id::double desc) = 1
