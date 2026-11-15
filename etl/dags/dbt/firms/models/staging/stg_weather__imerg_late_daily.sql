-- Staging for lake.weather_raw.imerg_late_daily: GPM IMERG Late (near real time) daily
-- precipitation per grid cell.
{{ config(tags=['weather']) }}
{{ stg_weather_imerg('imerg_late_daily', 'late') }}
