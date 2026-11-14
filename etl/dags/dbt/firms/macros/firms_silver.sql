{# Lake folder glob for one raw table (same layout as include/firms/config.py). #}
{% macro firms_lake_glob(table) -%}
{{ env_var('FIRMS_LAKE_PATH', '/usr/local/airflow/data/lake') }}/firms_raw/{{ table }}/*/*.parquet
{%- endmacro %}


{#
  Raw tables that have at least one file in the lake. A product FIRMS never
  returned rows for (e.g. VIIRS SNPP) has no folder, and read_parquet would fail.
  At parse time (execute = false) all tables are kept so dbt registers every
  source dependency.
#}
{% macro firms_present_tables(tables) %}
    {% if not execute %}
        {{ return(tables) }}
    {% endif %}
    {% set present = [] %}
    {% for t in tables %}
        {% set n = run_query("select count(*) from glob('" ~ firms_lake_glob(t) ~ "')").columns[0].values()[0] %}
        {% if n > 0 %}
            {% do present.append(t) %}
        {% endif %}
    {% endfor %}
    {{ return(present) }}
{% endmacro %}


{#
  Silver for one sensor: union of its raw tables, light normalization,
  latest version per detection, invalid rows kept and flagged in dq_issue.
  sensor: 'modis' | 'viirs'. Extra columns (e.g. SP `type`, lineage) pass through.
#}
{% macro firms_silver(sensor, tables) %}
{% set present = firms_present_tables(tables) %}
{% set brightness_col = 'brightness' if sensor == 'modis' else 'bright_ti4' %}
{% set aux_col = 'bright_t31' if sensor == 'modis' else 'bright_ti5' %}

with source as (
    {% if present | length == 0 %}
    {# Nothing landed yet (e.g. SP not published for this sensor): build an empty table instead of failing. #}
    {% do log("firms_silver: no files in the lake for " ~ tables | join(", ") ~ "; building an empty table", info=true) %}
    select
        null::double as latitude, null::double as longitude,
        null::double as {{ brightness_col }}, null::double as {{ aux_col }},
        null::double as scan, null::double as track, null::double as frp,
        null::date as acq_date, null::varchar as acq_time,
        null::varchar as satellite, null::varchar as instrument,
        null::varchar as confidence, null::varchar as version, null::varchar as daynight,
        null::varchar as _dlt_load_id, null::varchar as _dlt_id, null::date as load_date
    where false
    {% endif %}
    {% for t in present %}
    select * from {{ source('firms_raw', t) }}
    {% if not loop.last %}union all by name{% endif %}
    {% endfor %}
),

normalized as (
    select
        * exclude (_dlt_id, load_date) replace (
            lpad(trim(acq_time), 4, '0') as acq_time,
            nullif(trim(satellite), '') as satellite,
            nullif(trim(instrument), '') as instrument,
            {% if sensor == 'modis' %}
            try_cast(trim(confidence) as integer) as confidence,    -- 0-100
            {% else %}
            lower(nullif(trim(confidence), '')) as confidence,      -- l / n / h
            {% endif %}
            nullif(trim(version), '') as version,
            upper(nullif(trim(daynight), '')) as daynight
        ),
        try_strptime(acq_date::varchar || ' ' || lpad(trim(acq_time), 4, '0'), '%Y-%m-%d %H%M') as acq_datetime_utc
    from source
),

latest_version as (
    select *
    from normalized
    qualify row_number() over (
        partition by latitude, longitude, acq_date, acq_time, satellite
        order by _dlt_load_id::double desc
    ) = 1
)

select
    *,
    case
        when latitude is null or longitude is null or acq_datetime_utc is null or satellite is null
            then 'missing_key'
        when latitude not between -90 and 90 or longitude not between -180 and 180
            then 'invalid_coordinates'
        when {{ brightness_col }} is null or {{ brightness_col }} <= 0
            then 'invalid_brightness'
        when frp is null or frp < 0
            then 'invalid_frp'
        {% if sensor == 'modis' %}
        when confidence is null or confidence not between 0 and 100
        {% else %}
        when confidence is null or confidence not in ('l', 'n', 'h')
        {% endif %}
            then 'invalid_confidence'
        when daynight is null or daynight not in ('D', 'N')
            then 'invalid_daynight'
    end as dq_issue
from latest_version
{% endmacro %}
