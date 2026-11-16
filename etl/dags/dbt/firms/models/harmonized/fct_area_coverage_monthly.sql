-- What was actually downloaded: for each download area (bounding box in `_firms_area`) and month, whether MODIS and
-- VIIRS S-NPP cover every day of the month (union of the request windows of SP and NRT loads, bronze lineage; a window of
-- the whole world covers every area).
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

-- Each window as a box: a bounding box as requested, or the whole world ("world", what the scheduled ingest asks for by default).
boxes as (
    select sensor, area, start_date, n,
           case when area = 'world' then -180.0 else try_cast(string_split(area, ',')[1] as double) end as west,
           case when area = 'world' then -90.0 else try_cast(string_split(area, ',')[2] as double) end as south,
           case when area = 'world' then 180.0 else try_cast(string_split(area, ',')[3] as double) end as east,
           case when area = 'world' then 90.0 else try_cast(string_split(area, ',')[4] as double) end as north
    from windows
    where start_date is not null and n is not null and area not like 'local:%'
),

-- The areas whose coverage is reported: the bounding boxes that were downloaded ("world" is not one of them).
areas as (
    select distinct area, west, south, east, north from boxes
    where area <> 'world' and west is not null and south is not null and east is not null and north is not null
),

-- A window covers an area when its box holds the area's box, so a world download covers every area: otherwise the
-- months loaded by the scheduled ingest (area = world) would never count as covered for any area, and would be left out
-- of every comparison with the history (anomalies, the harmonized series' coverage).
covers as (
    select distinct b.sensor, a.area, b.start_date, b.n
    from boxes b
    join areas a on b.west <= a.west and b.east >= a.east and b.south <= a.south and b.north >= a.north
),

days as (
    select distinct sensor, area, unnest(generate_series(start_date, start_date + (n - 1)::integer, interval 1 day))::date as day
    from covers
),

by_month as (
    select sensor, area, date_trunc('month', day)::date as acq_month, count(*) as days_covered
    from days
    group by sensor, area, date_trunc('month', day)
)

select
    m.area,
    a.west, a.south, a.east, a.north,
    m.acq_month,
    coalesce(bool_or(m.days_covered = date_diff('day', m.acq_month, m.acq_month + interval 1 month)) filter (where m.sensor = 'modis'), false) as modis_complete,
    coalesce(bool_or(m.days_covered = date_diff('day', m.acq_month, m.acq_month + interval 1 month)) filter (where m.sensor = 'viirs'), false) as viirs_complete
from by_month m
join areas a using (area)
group by m.area, a.west, a.south, a.east, a.north, m.acq_month
