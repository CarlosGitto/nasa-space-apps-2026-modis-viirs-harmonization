"""
## FIRMS ingest — Standard Processing / historical (dlt)

Loads MODIS and VIIRS **standard (science-quality)** active fire detections
from the [FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) in
the bronze tables of the DuckLake (`lake.firms_raw.*_sp`, one table per
product). SP data is published with a lag of a few months and replaces NRT as
the reference record. Append-only: every load is kept as a new version; dbt
silver deduplicates.

**How a run picks its dates, per product:**
- `start_date` param set → loads from there (manual backfill of any range).
- otherwise → resumes the day after the last `acq_date` already loaded
  (or `SP_DEFAULT_START` if the table is empty).
- ends at `end_date` if set, else `max_days` after the start, never past
  what FIRMS reports as available for that product.

Requests are split into 5-day chunks (the API maximum). Reloading a range adds
a new version in the lake; silver keeps the latest one per detection.

On success it updates the `firms_raw_sp` asset, which triggers `firms_dbt_sp`.

Needs the Airflow connection `firms_default` (password = FIRMS MAP_KEY).
"""

from datetime import date, timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import FIRMS_AREA, FIRMS_RAW_SP, LAKE_WRITER_POOL

# First day loaded when a product has nothing in the lake yet. Keep it equal to
# WEATHER_DEFAULT_START (include/weather/config.py): weather covers the same dates.
SP_DEFAULT_START = date(2025, 1, 1)


@dag(
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    schedule="@weekly",
    catchup=False,
    max_active_runs=1,
    doc_md=__doc__,
    default_args={"owner": "etl", "retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        "area": Param(FIRMS_AREA, type="string", description='"world" or "west,south,east,north" (default: FIRMS_AREA)'),
        "start_date": Param(None, type=["null", "string"], format="date",
                            description="Empty = resume after the last loaded day"),
        "end_date": Param(None, type=["null", "string"], format="date",
                          description="Empty = start + max_days, capped by availability"),
        "max_days": Param(30, type="integer", minimum=1, maximum=366,
                          description="Days per product per run when end_date is empty"),
    },
    tags=["firms", "sp", "dlt", "ingest"],
)
def firms_ingest_sp():
    # The load writes to the DuckLake catalog: one writer at a time.
    @task(outlets=[FIRMS_RAW_SP], pool=LAKE_WRITER_POOL)
    def load_firms_sp(params=None):
        from include.firms.config import get_map_key, last_loaded_dates, raw_pipeline
        from include.firms.source import SP_PRODUCTS, fetch_availability, firms_sp_source

        map_key = get_map_key()
        yesterday = pendulum.now("UTC").date() - timedelta(days=1)
        forced_start = date.fromisoformat(params["start_date"]) if params["start_date"] else None
        forced_end = date.fromisoformat(params["end_date"]) if params["end_date"] else None
        max_days = int(params["max_days"])

        availability = fetch_availability(map_key)
        last_loaded = last_loaded_dates([p.lower() for p in SP_PRODUCTS])

        ranges = {}
        for product in SP_PRODUCTS:
            if forced_start:
                start = forced_start
            elif product.lower() in last_loaded:
                start = last_loaded[product.lower()] + timedelta(days=1)
            else:
                start = SP_DEFAULT_START
            end = forced_end or min(yesterday, start + timedelta(days=max_days - 1))

            if product in availability:
                first_available, last_available = availability[product]
                start, end = max(start, first_available), min(end, last_available)
            elif availability:
                print(f"{product}: not listed by FIRMS availability, skipping")
                continue

            if start > end:
                print(f"{product}: nothing new to load")
                continue
            ranges[product] = (start, end)
            print(f"{product}: loading {start} .. {end}")

        if not ranges:
            # Skipped tasks don't update the asset, so firms_dbt_sp isn't triggered for nothing.
            from airflow.sdk.exceptions import AirflowSkipException

            raise AirflowSkipException("All SP products are up to date")

        pipeline = raw_pipeline("firms_sp")
        load_info = pipeline.run(
            firms_sp_source(map_key=map_key, ranges=ranges, area=params["area"]),
        )
        print(load_info)
        print(pipeline.last_trace.last_normalize_info)

    load_firms_sp()


firms_ingest_sp()
