{{ config(alias="viirs_sp") }}
-- VIIRS SP (all VIIRS satellites): normalized, latest version per detection, invalid rows flagged in dq_issue.
{{ firms_silver("viirs", ["viirs_snpp_sp", "viirs_noaa20_sp", "viirs_noaa21_sp"]) }}
