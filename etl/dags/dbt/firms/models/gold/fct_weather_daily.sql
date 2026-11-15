-- WEATHER of a grid cell (MERRA-2 grid, 0.5° x 0.625°, ~55 km) per UTC day, near fires:
-- what the map's weather layers draw (max temperature, rain, mean wind). NASA POWER daily
-- values. UTC days, like FIRMS acq_date, so it joins on the same date.
--
-- Keyed by the cell center (cell_lat, cell_lon), not by hexagon: one cell covers many
-- hexagons; the backend picks the cell under each point. The hexagon panel asks POWER
-- directly for its point (any place, more variables): this table only feeds the map.
--
-- A view over silver: daily values need no further aggregation.
{{ config(materialized='view', contract={'enforced': true}) }}

select
    cell_lat,
    cell_lon,
    obs_date,
    obs_month,
    t2m_max_c,
    precipitation_mm,
    wind_speed_mean_ms,
    wind_dir_mean_deg,
    _dlt_load_id as last_load_id
from {{ ref('int_weather_power_daily') }}
where dq_issue is null
