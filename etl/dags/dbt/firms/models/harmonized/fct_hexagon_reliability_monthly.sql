-- How far to trust the detections of a hexagon, month by month and sensor: share of persistent detections and the
-- mean reliability index (probability that an independent sensor confirms a detection; see hrm_detection_reliability).
-- Sensors are not added together. No row means "no detection", not "no fire".
{{ config(materialized='table', sorted_by=['h3_r6'], tags=['harmonized']) }}

select
    h3_r6,
    acq_month,
    sensor,
    count(*)::bigint as detections,
    count(*) filter (where persistent)::bigint as persistent_detections,
    (count(*) filter (where persistent) / count(*))::double as persistent_share,
    avg(reliability)::double as reliability_mean,
    sum(reliability)::double as reliability_sum  -- expected number of detections an independent sensor confirms
from {{ ref('hrm_detection_reliability') }}
group by h3_r6, acq_month, sensor
