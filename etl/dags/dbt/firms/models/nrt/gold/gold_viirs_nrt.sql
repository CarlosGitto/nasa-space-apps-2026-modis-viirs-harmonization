{{ config(alias="viirs_nrt") }}
-- Clean VIIRS NRT detections (silver rows with no dq_issue), lineage columns kept.
select * exclude (dq_issue)
from {{ ref("silver_viirs_nrt") }}
where dq_issue is null
