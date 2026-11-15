-- reliability is a probability: in (0, 1) for every detection of the latest load
select detection_id, reliability from {{ ref('hrm_detection_reliability') }}
where reliability is null or reliability <= 0 or reliability >= 1
limit 10
