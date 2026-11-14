{{ config(alias="modis_sp") }}
-- Clean MODIS SP detections (silver rows with no dq_issue), lineage columns kept.
select * exclude (dq_issue)
from {{ ref("silver_modis_sp") }}
where dq_issue is null
