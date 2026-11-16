-- What was actually downloaded: for each download area (bounding box in `_firms_area`) and month, whether MODIS and
-- VIIRS S-NPP cover every day of the month (union of the request windows of SP and NRT loads, bronze lineage).
--
-- Why it exists: the facts only have rows where there was a detection, so "no row" means "no detection" ONLY where
-- the data was downloaded. Outside a covered area-month it means "we have no data". The harmonized series and the
-- k calibration use this to tell the two apart. A hexagon is covered in a month when its centre lies in an area
-- whose coverage of the sensor that defines that month (MODIS before 2012-02, VIIRS after) is complete.
{{ config(materialized='table', tags=['harmonized']) }}

with windows as (
    -- windows downloaded by the API ingest (Airflow): the lineage columns say exactly what was requested
    select distinct 'modis' as sensor, _firms_area as area, _firms_request_start as start_date, _firms_request_days as n
    from {{ source('firms_raw', 'modis_sp') }} where _firms_url not like 'local-file://%'
    union
    select distinct 'viirs', _firms_area, _firms_request_start, _firms_request_days
    from {{ source('firms_raw', 'viirs_snpp_sp') }} where _firms_url not like 'local-file://%'
    {% if firms_table_exists('modis_nrt') %}
    union
    select distinct 'modis', _firms_area, _firms_request_start, _firms_request_days from {{ source('firms_raw', 'modis_nrt') }}
    {% endif %}
    {% if firms_table_exists('viirs_snpp_nrt') %}
    union
    select distinct 'viirs', _firms_area, _firms_request_start, _firms_request_days from {{ source('firms_raw', 'viirs_snpp_nrt') }}
    {% endif %}
    {% if firms_table_exists('download_windows') %}
    -- windows of files loaded by hand (include.firms.load_local): ALL of them, also those with no detections,
    -- which leave no row in the detection tables
    union
    select distinct case when product like 'MODIS%' then 'modis' else 'viirs' end, area, start_date, days
    from {{ source('firms_raw', 'download_windows') }} where product in ('MODIS_SP', 'MODIS_NRT', 'VIIRS_SNPP_SP', 'VIIRS_SNPP_NRT')
    {% endif %}
),

days as (
    select distinct sensor, area, unnest(generate_series(start_date, start_date + (n - 1)::integer, interval 1 day))::date as day
    from windows
    where start_date is not null and n is not null and area not like 'local:%'
),

by_month as (
    select sensor, area, date_trunc('month', day)::date as acq_month, count(*) as days_covered
    from days
    group by sensor, area, date_trunc('month', day)
)

select
    area,
    try_cast(string_split(area, ',')[1] as double) as west,
    try_cast(string_split(area, ',')[2] as double) as south,
    try_cast(string_split(area, ',')[3] as double) as east,
    try_cast(string_split(area, ',')[4] as double) as north,
    acq_month,
    coalesce(bool_or(days_covered = date_diff('day', acq_month, acq_month + interval 1 month)) filter (where sensor = 'modis'), false) as modis_complete,
    coalesce(bool_or(days_covered = date_diff('day', acq_month, acq_month + interval 1 month)) filter (where sensor = 'viirs'), false) as viirs_complete
from by_month
group by area, acq_month
