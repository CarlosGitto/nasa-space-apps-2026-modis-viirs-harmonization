-- Staging for lake.weather_raw.imerg_final_daily: GPM IMERG Final (research grade) daily
-- precipitation per grid cell.
{{ config(tags=['weather']) }}
{{ stg_weather_imerg('imerg_final_daily', 'final') }}
