{#
  Whether a bronze table exists yet. A product that never returned rows has no
  table: e.g. VIIRS S-NPP NRT (retired in Nov 2026), or weather before its first
  load. At parse time (execute = false) assume
  it does, so dbt registers the source dependency.
#}
{% macro source_table_exists(source_name, table) %}
    {% if not execute %}
        {{ return(true) }}
    {% endif %}
    {% set src = source(source_name, table) %}
    {% set n = run_query(
        "select count(*) from information_schema.tables where table_catalog = '" ~ src.database
        ~ "' and table_schema = '" ~ src.schema ~ "' and table_name = '" ~ table ~ "'"
    ).columns[0].values()[0] %}
    {{ return(n > 0) }}
{% endmacro %}


{% macro firms_table_exists(table) %}
    {{ return(source_table_exists('firms_raw', table)) }}
{% endmacro %}


{#
  Staging for one bronze table: cleaning, types, data-quality flag, origin
  columns and the hexagon. Every staging model has the same columns whatever
  the sensor or product, so the intermediate layer can union them by name.

    table    bronze table in lake.firms_raw, e.g. 'viirs_noaa20_nrt'
    sensor   'modis' | 'viirs'
    product  'sp' | 'nrt'

  Invalid rows are kept and flagged in dq_issue (null = valid), never dropped.
#}
{% macro stg_firms(table, sensor, product) %}
{% set bright = 'brightness' if sensor == 'modis' else 'bright_ti4' %}
{% set bright_aux = 'bright_t31' if sensor == 'modis' else 'bright_ti5' %}
{% set has_type = product == 'sp' %}

with raw as (
    {% if firms_table_exists(table) %}
    select * from {{ source('firms_raw', table) }}
    {% else %}
    {# No bronze table yet: same columns, no rows. #}
    select
        null::double as latitude, null::double as longitude,
        null::double as {{ bright }}, null::double as {{ bright_aux }},
        null::double as scan, null::double as track, null::double as frp,
        null::date as acq_date, null::varchar as acq_time,
        null::varchar as satellite, null::varchar as instrument,
        null::varchar as confidence, null::varchar as version, null::varchar as daynight,
        {% if has_type %}null::varchar as type,{% endif %}
        null::varchar as _firms_product, null::varchar as _firms_area,
        null::date as _firms_request_start, null::bigint as _firms_request_days,
        null::varchar as _firms_url, null::timestamptz as _extracted_at,
        null::varchar as _dlt_load_id, null::varchar as _dlt_id
    where false
    {% endif %}
),

typed as (
    select
        '{{ sensor }}' as sensor,
        case nullif(trim(satellite), '')
            when 'T' then 'Terra' when 'Terra' then 'Terra'
            when 'A' then 'Aqua' when 'Aqua' then 'Aqua'
            when 'N' then 'S-NPP'
            when 'N20' then 'NOAA-20' when '1' then 'NOAA-20'
            when 'N21' then 'NOAA-21'
            else nullif(trim(satellite), '')
        end as satellite,
        nullif(trim(satellite), '') as satellite_code,
        '{{ product }}' as product,
        '{{ table }}' as source_table,
        nullif(trim(version), '') as version,
        nullif(trim(instrument), '') as instrument,
        latitude,
        longitude,
        acq_date,
        lpad(trim(acq_time), 4, '0') as acq_time,
        upper(nullif(trim(daynight), '')) as daynight,
        frp,
        scan,
        track,
        {{ bright }} as brightness,
        {{ bright_aux }} as brightness_aux,
        nullif(trim(confidence), '') as confidence_raw,
        {% if has_type %}try_cast(trim(type) as integer){% else %}null::integer{% endif %} as fire_type,
        _dlt_load_id,
        _firms_product,
        _firms_area,
        _firms_request_start,
        _firms_request_days,
        _firms_url,
        _extracted_at
    from raw
),

enriched as (
    select
        md5(concat_ws('|', sensor, satellite_code, latitude::varchar, longitude::varchar,
                      acq_date::varchar, acq_time)) as detection_id,
        *,
        try_strptime(acq_date::varchar || ' ' || acq_time, '%Y-%m-%d %H%M') as acq_datetime_utc,
        date_trunc('month', acq_date)::date as acq_month,
        {% if sensor == 'modis' %}
        case
            when try_cast(confidence_raw as integer) is null then null
            when try_cast(confidence_raw as integer) < 30 then 'low'
            when try_cast(confidence_raw as integer) < 80 then 'nominal'
            else 'high'
        end as confidence_level,
        {% else %}
        case lower(confidence_raw) when 'l' then 'low' when 'n' then 'nominal' when 'h' then 'high' end as confidence_level,
        {% endif %}
        case when latitude between -90 and 90 and longitude between -180 and 180
             then h3_latlng_to_cell(latitude, longitude, 6) end as h3_r6,
        case when latitude between -90 and 90 and longitude between -180 and 180
             then h3_latlng_to_cell(latitude, longitude, 8) end as h3_r8
    from typed
)

select
    * exclude (_dlt_load_id, _firms_product, _firms_area, _firms_request_start, _firms_request_days, _firms_url, _extracted_at),
    -- Hour of the day in local solar time (UTC + longitude / 15): the fire's diurnal cycle.
    ((extract(hour from acq_datetime_utc) + extract(minute from acq_datetime_utc) / 60.0
      + longitude / 15.0) % 24 + 24) % 24 as solar_hour_local,
    case
        when latitude is null or longitude is null or acq_datetime_utc is null or satellite is null
            then 'missing_key'
        when latitude not between -90 and 90 or longitude not between -180 and 180
            then 'invalid_coordinates'
        when brightness is null or brightness <= 0
            then 'invalid_brightness'
        when frp is null or frp < 0
            then 'invalid_frp'
        {% if sensor == 'modis' %}
        when try_cast(confidence_raw as integer) is null or try_cast(confidence_raw as integer) not between 0 and 100
        {% else %}
        when confidence_level is null
        {% endif %}
            then 'invalid_confidence'
        when daynight is null or daynight not in ('D', 'N')
            then 'invalid_daynight'
    end as dq_issue,
    -- lineage
    _dlt_load_id,
    _firms_product,
    _firms_area,
    _firms_request_start,
    _firms_request_days,
    _firms_url,
    _extracted_at
from enriched
{% endmacro %}
