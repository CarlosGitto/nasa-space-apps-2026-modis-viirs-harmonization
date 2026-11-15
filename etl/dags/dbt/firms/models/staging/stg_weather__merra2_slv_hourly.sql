-- Staging for lake.weather_raw.merra2_slv_hourly: MERRA-2 hourly near-surface weather per
-- grid cell in °C and m/s, with relative humidity, VPD, wind speed and direction.
{{ config(tags=['weather']) }}
{{ stg_weather_merra2('merra2_slv_hourly') }}
