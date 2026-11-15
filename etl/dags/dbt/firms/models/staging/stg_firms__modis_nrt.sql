-- Staging for lake.firms_raw.modis_nrt: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['nrt']) }}
{{ stg_firms('modis_nrt', 'modis', 'nrt') }}
