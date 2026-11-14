{{ config(alias="modis_nrt") }}
-- Clean MODIS NRT detections (silver rows with no dq_issue), lineage columns kept.
select * exclude (dq_issue)
from {{ ref("silver_modis_nrt") }}
where dq_issue is null
