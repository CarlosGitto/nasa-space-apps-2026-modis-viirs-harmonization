-- WEATHER of a MERRA-2 grid cell (0.5° x 0.625°, ~55 km) per UTC day: the daily
-- extremes and means that matter for fire (heat, dryness, wind). UTC days, like
-- FIRMS acq_date, so it joins on the same date. `hours` < 24 means a partial day.
--
-- Keyed by the cell center (cell_lat, cell_lon), not by hexagon: one cell covers
-- many hexagons, and the backend picks the cell nearest to a hexagon's center.
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='obs_month',
          partitioned_by=['obs_month'], sorted_by=['cell_lat', 'cell_lon'], on_schema_change='fail',
          contract={'enforced': true}) }}
{% set source_sql %}
    select * from {{ ref('int_weather_merra2_hourly') }} where dq_issue is null
{% endset %}

with hourly as (
    select * from ({{ source_sql }})
    where {{ pending_months_scope(source_sql, 'obs_date', var('weather_months_per_run', 2), target_load_col='last_load_id') }}
)

select
    cell_lat,
    cell_lon,
    obs_date,
    obs_month,
    max(t2m_c)::double as t2m_max_c,
    min(t2m_c)::double as t2m_min_c,
    avg(t2m_c)::double as t2m_mean_c,
    avg(dewpoint_c)::double as dewpoint_mean_c,
    min(rh_pct)::double as rh_min_pct,
    avg(rh_pct)::double as rh_mean_pct,
    max(vpd_kpa)::double as vpd_max_kpa,
    avg(vpd_kpa)::double as vpd_mean_kpa,
    max(wind_speed_ms)::double as wind_speed_max_ms,
    avg(wind_speed_ms)::double as wind_speed_mean_ms,
    -- Direction of the mean wind vector (not the mean of angles), where it comes FROM.
    ((degrees(atan2(-avg(u10_ms), -avg(v10_ms))) + 360) % 360)::double as wind_dir_mean_deg,
    count(*)::bigint as hours,
    max(_dlt_load_id) as last_load_id
from hourly
group by cell_lat, cell_lon, obs_date, obs_month
