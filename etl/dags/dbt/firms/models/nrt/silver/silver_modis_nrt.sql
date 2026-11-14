{{ config(alias="modis_nrt") }}
-- MODIS NRT: normalized, latest version per detection, invalid rows flagged in dq_issue.
{{ firms_silver("modis", ["modis_nrt"]) }}
