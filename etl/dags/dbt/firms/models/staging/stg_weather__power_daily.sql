-- Staging for lake.weather_raw.power_daily: NASA POWER daily weather per grid cell (MERRA-2
-- grid, 0.5° x 0.625°) and UTC day, near fires. Values come in usual units already (°C, mm,
-- m/s, degrees); invalid rows are kept and flagged in dq_issue (null = valid).
{{ config(tags=['weather']) }}

with raw as (
    {% if source_table_exists('weather_raw', 'power_daily') %}
    select * from {{ source('weather_raw', 'power_daily') }}
    {% else %}
    select null::date as obs_date, null::double as lat, null::double as lon, null::double as t2m_max,
           null::double as precipitation, null::double as ws10m, null::double as wd10m,
           null::varchar as _box, null::varchar as _area, null::varchar as _source,
           null::timestamptz as _extracted_at, null::varchar as _dlt_load_id
    where false
    {% endif %}
)

select
    lat::double as cell_lat,
    lon::double as cell_lon,
    obs_date,                                         -- UTC day, like FIRMS acq_date
    date_trunc('month', obs_date)::date as obs_month,
    t2m_max::double as t2m_max_c,                     -- daily max 2 m temperature
    precipitation::double as precipitation_mm,        -- daily total (PRECTOTCORR)
    ws10m::double as wind_speed_mean_ms,              -- daily mean 10 m wind speed
    wd10m::double as wind_dir_mean_deg,               -- where the wind comes from, 0 = north
    case
        when lat is null or lon is null or obs_date is null then 'missing_key'
        when t2m_max is not null and t2m_max not between -90 and 60 then 'invalid_temperature'
        when precipitation < 0 then 'invalid_precipitation'
        when ws10m < 0 or wd10m not between 0 and 360 then 'invalid_wind'
    end as dq_issue,
    -- lineage
    _box, _area, _source, _extracted_at, _dlt_load_id
from raw
