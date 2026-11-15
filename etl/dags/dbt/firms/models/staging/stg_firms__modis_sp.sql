-- Staging for lake.firms_raw.modis_sp: cleaned, typed, hexagon assigned, invalid rows flagged.
{{ config(tags=['sp']) }}
{{ stg_firms('modis_sp', 'modis', 'sp') }}
