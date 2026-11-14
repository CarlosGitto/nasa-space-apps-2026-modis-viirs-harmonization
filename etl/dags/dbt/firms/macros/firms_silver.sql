{#
  Raw tables that exist in the lake's bronze schema. A product FIRMS never
  returned rows for (e.g. VIIRS SNPP NRT) has no table yet. At parse time
  (execute = false) all tables are kept so dbt registers every source dependency.
#}
{% macro firms_present_tables(tables) %}
    {% if not execute %}
        {{ return(tables) }}
    {% endif %}
    {% set first = source('firms_raw', tables[0]) %}
    {% set existing = run_query(
        "select table_name from information_schema.tables where table_catalog = '"
        ~ first.database ~ "' and table_schema = '" ~ first.schema ~ "'"
    ).columns[0].values() %}
    {% set present = [] %}
    {% for t in tables %}
        {% if t in existing %}
            {% do present.append(t) %}
        {% endif %}
    {% endfor %}
    {{ return(present) }}
{% endmacro %}


{#
  Incremental scope: the acquisition months that received rows from loads newer
  than anything silver already has. Returns a SQL predicate on the raw rows.

  Recomputing whole months is correct because the dedup key includes acq_date:
  every version of a detection falls in the same month. The month list and the
  watermark are rendered as literals (not subqueries) so DuckLake can skip files
  using its min/max column statistics.

  _dlt_load_id is compared as text: it is "<unix seconds>.<fraction>" with a
  10-digit integer part (until year 2286), so text order matches numeric order.
#}
{% macro firms_incremental_scope(raw_sql) %}
    {% set watermark = run_query("select max(_dlt_load_id) from " ~ this).columns[0].values()[0] %}
    {% if watermark is none %}
        {{ return("true") }}
    {% endif %}
    {% set months = run_query(
        "select distinct strftime(date_trunc('month', acq_date), '%Y-%m-%d') from (" ~ raw_sql ~ ") "
        ~ "where _dlt_load_id > '" ~ watermark ~ "' and acq_date is not null order by 1"
    ).columns[0].values() %}
    {% do log("firms_silver " ~ this ~ ": watermark " ~ watermark ~ ", months to recompute: " ~ (months | join(", ") or "none"), info=true) %}
    {% set new_without_date = "(acq_date is null and _dlt_load_id > '" ~ watermark ~ "')" %}
    {% if months | length == 0 %}
        {{ return(new_without_date) }}
    {% endif %}
    {% set month_list = [] %}
    {% for m in months %}
        {% do month_list.append("date '" ~ m ~ "'") %}
    {% endfor %}
    {{ return(
        "((acq_date >= date '" ~ months[0] ~ "' and acq_date < date '" ~ months[-1] ~ "' + interval 1 month"
        ~ " and date_trunc('month', acq_date) in (" ~ month_list | join(", ") ~ ")) or " ~ new_without_date ~ ")"
    ) }}
{% endmacro %}


{#
  Silver for one sensor: union of its raw tables, light normalization,
  latest version per detection, invalid rows kept and flagged in dq_issue.
  sensor: 'modis' | 'viirs'. Extra columns (e.g. SP `type`, lineage) pass through.

  Incremental (see dbt_project.yml): only the touched acquisition months are
  recomputed and replaced (delete+insert on acq_month).
#}
{% macro firms_silver(sensor, tables) %}
{% set present = firms_present_tables(tables) %}
{% set brightness_col = 'brightness' if sensor == 'modis' else 'bright_ti4' %}
{% set aux_col = 'bright_t31' if sensor == 'modis' else 'bright_ti5' %}

{% set raw_sql %}
    {% if present | length == 0 %}
    {# Nothing landed yet (e.g. SP not published for this sensor): build an empty table instead of failing. #}
    select
        null::double as latitude, null::double as longitude,
        null::double as {{ brightness_col }}, null::double as {{ aux_col }},
        null::double as scan, null::double as track, null::double as frp,
        null::date as acq_date, null::varchar as acq_time,
        null::varchar as satellite, null::varchar as instrument,
        null::varchar as confidence, null::varchar as version, null::varchar as daynight,
        null::varchar as _dlt_load_id, null::varchar as _dlt_id
    where false
    {% endif %}
    {% for t in present %}
    select * from {{ source('firms_raw', t) }}
    {% if not loop.last %}union all by name{% endif %}
    {% endfor %}
{% endset %}

{% if present | length == 0 %}
    {% do log("firms_silver: no bronze tables yet for " ~ tables | join(", ") ~ "; building an empty table", info=true) %}
{% endif %}

with source as (
    select * from ({{ raw_sql }})
    {% if is_incremental() and execute %}
    where {{ firms_incremental_scope(raw_sql) }}
    {% endif %}
),

normalized as (
    select
        * exclude (_dlt_id) replace (
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
        date_trunc('month', acq_date)::date as acq_month,
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
