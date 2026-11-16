-- Staging for lake.firms_raw.viirs_snpp_sp: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['sp']) }}
{{ stg_firms('viirs_snpp_sp', 'viirs', 'sp') }}
