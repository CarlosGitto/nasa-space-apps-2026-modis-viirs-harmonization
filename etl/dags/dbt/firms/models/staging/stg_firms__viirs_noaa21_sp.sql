-- Staging for lake.firms_raw.viirs_noaa21_sp: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['sp']) }}
{{ stg_firms('viirs_noaa21_sp', 'viirs', 'sp') }}
