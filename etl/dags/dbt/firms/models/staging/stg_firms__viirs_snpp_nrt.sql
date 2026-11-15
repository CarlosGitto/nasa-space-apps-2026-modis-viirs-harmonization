-- Staging for lake.firms_raw.viirs_snpp_nrt: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['nrt']) }}
{{ stg_firms('viirs_snpp_nrt', 'viirs', 'nrt') }}
