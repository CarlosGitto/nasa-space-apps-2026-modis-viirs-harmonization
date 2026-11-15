{#
  Staging for the weather bronze tables (lake.weather_raw): one row per grid
  cell and time step, in usual units, with the derived variables. Same idea as
  stg_firms: invalid rows are kept and flagged in dq_issue (null = valid).

  The grid cell is identified by its center (cell_lat, cell_lon), exactly as
  the file gives it: MERRA-2 and IMERG are different grids and never mixed.
#}

{#
  Saturation vapour pressure over water in kPa for a temperature in °C (Magnus
  formula, Alduchov & Eskridge 1996 coefficients). Of the dew point it gives
  the actual vapour pressure.
#}
{% macro saturation_vapour_pressure_kpa(temp_c) -%}
    (0.61094 * exp(17.625 * ({{ temp_c }}) / (({{ temp_c }}) + 243.04)))
{%- endmacro %}


{# Empty relation with the bronze lineage columns, for a table that doesn't exist yet. #}
{% macro _weather_lineage_nulls() -%}
    null::varchar as _granule, null::varchar as _collection, null::varchar as _area,
    null::timestamptz as _extracted_at, null::varchar as _dlt_load_id, null::varchar as _dlt_id
{%- endmacro %}


{% macro stg_weather_merra2(table) %}
with raw as (
    {% if source_table_exists('weather_raw', table) %}
    select * from {{ source('weather_raw', table) }}
    {% else %}
    select null::date as obs_date, null::timestamptz as time_utc, null::double as lat, null::double as lon,
           null::float as t2m, null::float as t2mdew, null::float as u10m, null::float as v10m,
           {{ _weather_lineage_nulls() }}
    where false
    {% endif %}
),

typed as (
    select
        lat::double as cell_lat,
        lon::double as cell_lon,
        time_utc,                                   -- mean over the hour, stamped at HH:30
        obs_date,                                   -- UTC day, like FIRMS acq_date
        date_trunc('month', obs_date)::date as obs_month,
        (t2m - 273.15)::double as t2m_c,
        (t2mdew - 273.15)::double as dewpoint_c,
        u10m::double as u10_ms,                     -- eastward
        v10m::double as v10_ms,                     -- northward
        _granule, _collection, _area, _extracted_at, _dlt_load_id
    from raw
)

select
    * exclude (_granule, _collection, _area, _extracted_at, _dlt_load_id),
    sqrt(u10_ms * u10_ms + v10_ms * v10_ms) as wind_speed_ms,
    -- Meteorological convention: where the wind comes FROM, 0 = north, 90 = east.
    (degrees(atan2(-u10_ms, -v10_ms)) + 360) % 360 as wind_dir_deg,
    -- Dew point above air temperature (supersaturation) can appear in reanalysis: capped at 100 %.
    least(100, 100 * {{ saturation_vapour_pressure_kpa('dewpoint_c') }} / {{ saturation_vapour_pressure_kpa('t2m_c') }}) as rh_pct,
    greatest(0, {{ saturation_vapour_pressure_kpa('t2m_c') }} - {{ saturation_vapour_pressure_kpa('dewpoint_c') }}) as vpd_kpa,
    case
        when cell_lat is null or cell_lon is null or time_utc is null then 'missing_key'
        when t2m_c is null or dewpoint_c is null or u10_ms is null or v10_ms is null then 'missing_value'
        when t2m_c not between -90 and 60 or dewpoint_c not between -100 and 40 then 'invalid_temperature'
    end as dq_issue,
    -- lineage
    _granule, _collection, _area, _extracted_at, _dlt_load_id
from typed
{% endmacro %}


{#
    table   bronze table, e.g. 'imerg_final_daily'
    run     'final' (research grade) | 'late' (near real time)
#}
{% macro stg_weather_imerg(table, run) %}
with raw as (
    {% if source_table_exists('weather_raw', table) %}
    select * from {{ source('weather_raw', table) }}
    {% else %}
    select null::date as obs_date, null::timestamptz as time_utc, null::double as lat, null::double as lon,
           null::float as precipitation, {{ _weather_lineage_nulls() }}
    where false
    {% endif %}
)

select
    lat::double as cell_lat,
    lon::double as cell_lon,
    obs_date,                                       -- UTC day (00:00-24:00 UTC)
    date_trunc('month', obs_date)::date as obs_month,
    '{{ run }}' as imerg_run,
    precipitation::double as precipitation_mm,      -- daily total
    case
        when lat is null or lon is null or obs_date is null then 'missing_key'
        when precipitation is null then 'missing_value'
        when precipitation < 0 then 'invalid_precipitation'
    end as dq_issue,
    -- lineage
    _granule, _collection, _area, _extracted_at, _dlt_load_id
from raw
{% endmacro %}
