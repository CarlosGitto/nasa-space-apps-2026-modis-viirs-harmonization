-- PRECIPITATION of a GPM IMERG grid cell (0.1°, ~10 km) per UTC day, one value per
-- cell and day: Final where published, Late after it (imerg_run). A view over silver:
-- no transformation, so no copy of ~120 M rows per year of South America.
--
-- Accumulations (last 7 / 30 days, days since rain) are not stored: they depend on the
-- window asked for, and the backend computes them for one cell's series.
{{ config(materialized='view', contract={'enforced': true}) }}

select
    cell_lat,
    cell_lon,
    obs_date,
    obs_month,
    precipitation_mm,
    imerg_run,
    _dlt_load_id as last_load_id
from {{ ref('int_weather_imerg_daily') }}
where dq_issue is null
