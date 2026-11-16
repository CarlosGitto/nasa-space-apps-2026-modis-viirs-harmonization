-- Staging for lake.firms_raw.viirs_noaa21_nrt: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['nrt']) }}
{{ stg_firms('viirs_noaa21_nrt', 'viirs', 'nrt') }}
