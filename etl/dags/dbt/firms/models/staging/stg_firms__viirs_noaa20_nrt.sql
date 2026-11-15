-- Staging for lake.firms_raw.viirs_noaa20_nrt: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['nrt']) }}
{{ stg_firms('viirs_noaa20_nrt', 'viirs', 'nrt') }}
