"""
## FIRMS ingest — NRT (dlt)

Lands MODIS and VIIRS **near-real-time** active fire detections from the
[FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) in the bronze
tables of the DuckLake (`lake.firms_raw.*_nrt`, one table per product).
Append-only: every load is kept as a new version; dbt silver deduplicates.

Each scheduled run covers the day of its data interval plus the `day_range - 1`
days before it, so detections that FIRMS publishes late are picked up on the
next run.

**Backfill:** set `start_date` (and optionally `end_date`, default yesterday) to
load a range instead, e.g. the NRT period after the last published SP month so
the series is continuous. Each product is clamped to the dates FIRMS has (NRT is
only kept for the last few months), in 5-day requests.

On success it updates the `firms_raw_nrt` asset, which triggers `firms_dbt_nrt`.

Needs the Airflow connection `firms_default` (password = FIRMS MAP_KEY).
"""

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import FIRMS_AREA, FIRMS_RAW_NRT, LAKE_WRITER_POOL


@dag(
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    doc_md=__doc__,
    default_args={"owner": "etl", "retries": 2, "retry_delay": timedelta(minutes=5)},
    params={
        "area": Param(FIRMS_AREA, type="string", description='"world" or "west,south,east,north" (default: FIRMS_AREA)'),
        "day_range": Param(2, type="integer", minimum=1, maximum=5),
        "start_date": Param(None, type=["null", "string"], format="date",
                            description="Backfill: first day to load (empty = scheduled rolling window)"),
        "end_date": Param(None, type=["null", "string"], format="date",
                          description="Backfill: last day to load (empty = yesterday)"),
    },
    tags=["firms", "nrt", "dlt", "ingest"],
)
def firms_ingest_nrt():
    # The load writes to the DuckLake catalog: one writer at a time.
    @task(outlets=[FIRMS_RAW_NRT], pool=LAKE_WRITER_POOL)
    def load_firms_nrt(params=None, data_interval_start=None):
        from datetime import date

        from include.firms.config import get_map_key, raw_pipeline
        from include.firms.source import NRT_PRODUCTS, fetch_availability, firms_nrt_source

        map_key = get_map_key()
        yesterday = pendulum.now("UTC").date() - timedelta(days=1)

        if params["start_date"]:
            # Backfill: the requested range, clamped per product to what FIRMS has.
            start = date.fromisoformat(params["start_date"])
            end = date.fromisoformat(params["end_date"]) if params["end_date"] else yesterday
            availability = fetch_availability(map_key)
            ranges = {}
            for product in NRT_PRODUCTS:
                lo, hi = start, end
                if product in availability:
                    lo, hi = max(lo, availability[product][0]), min(hi, availability[product][1])
                if lo <= hi:
                    ranges[product] = (lo, hi)
                    print(f"{product}: loading {lo} .. {hi}")
                else:
                    print(f"{product}: nothing available in {start} .. {end}")
            if not ranges:
                from airflow.sdk.exceptions import AirflowSkipException

                raise AirflowSkipException("No NRT data available for that range")
        else:
            # Scheduled: the run's day plus the day_range - 1 days before it.
            end = (data_interval_start or pendulum.now("UTC").subtract(days=1)).date()
            start = end - timedelta(days=int(params["day_range"]) - 1)
            ranges = {product: (start, end) for product in NRT_PRODUCTS}

        pipeline = raw_pipeline("firms_nrt")
        load_info = pipeline.run(firms_nrt_source(map_key=map_key, ranges=ranges, area=params["area"]))
        print(load_info)
        print(pipeline.last_trace.last_normalize_info)

    load_firms_nrt()


firms_ingest_nrt()
