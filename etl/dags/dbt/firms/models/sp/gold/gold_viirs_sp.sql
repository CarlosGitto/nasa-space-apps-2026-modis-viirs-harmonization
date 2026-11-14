{{ config(alias="viirs_sp") }}
-- Clean VIIRS SP detections (silver rows with no dq_issue), lineage columns kept.
select * exclude (dq_issue)
from {{ ref("silver_viirs_sp") }}
where dq_issue is null
