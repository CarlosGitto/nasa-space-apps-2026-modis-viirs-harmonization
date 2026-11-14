{{ config(alias="modis_sp") }}
-- MODIS SP: normalized, latest version per detection, invalid rows flagged in dq_issue.
{{ firms_silver("modis", ["modis_sp"]) }}
