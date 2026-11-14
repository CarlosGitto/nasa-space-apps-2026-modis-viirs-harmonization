{{ config(alias="viirs_nrt") }}
-- VIIRS NRT (all VIIRS satellites): normalized, latest version per detection, invalid rows flagged in dq_issue.
{{ firms_silver("viirs", ["viirs_snpp_nrt", "viirs_noaa20_nrt", "viirs_noaa21_nrt"]) }}
