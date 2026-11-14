"""
## FIRMS ingest — NRT (dlt)

Lands MODIS and VIIRS **near-real-time** active fire detections from the
[FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) in the bronze
tables of the DuckLake (`lake.firms_raw.*_nrt`, one table per product).
Append-only: every load is kept as a new version; dbt silver deduplicates.

Each run covers the day of its data interval plus the `day_range - 1` days
before it, so detections that FIRMS publishes late are picked up on the next
run.

On success it updates the `firms_raw_nrt` asset, which triggers `firms_dbt_nrt`.

Needs the Airflow connection `firms_default` (password = FIRMS MAP_KEY).
"""

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import FIRMS_RAW_NRT, LAKE_WRITER_POOL


@dag(
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    doc_md=__doc__,
    default_args={"owner": "etl", "retries": 2, "retry_delay": timedelta(minutes=5)},
    params={
        "area": Param("world", type="string", description='"world" or "west,south,east,north"'),
        "day_range": Param(2, type="integer", minimum=1, maximum=5),
    },
    tags=["firms", "nrt", "dlt", "ingest"],
)
def firms_ingest_nrt():
    # The load writes to the DuckLake catalog: one writer at a time.
    @task(outlets=[FIRMS_RAW_NRT], pool=LAKE_WRITER_POOL)
    def load_firms_nrt(params=None, data_interval_start=None):
        from include.firms.config import get_map_key, raw_pipeline
        from include.firms.source import NRT_PRODUCTS, firms_nrt_source

        end = (data_interval_start or pendulum.now("UTC").subtract(days=1)).date()
        start = end - timedelta(days=int(params["day_range"]) - 1)

        pipeline = raw_pipeline("firms_nrt")
        load_info = pipeline.run(
            firms_nrt_source(
                map_key=get_map_key(),
                ranges={product: (start, end) for product in NRT_PRODUCTS},
                area=params["area"],
            ),
        )
        print(load_info)
        print(pipeline.last_trace.last_normalize_info)

    load_firms_nrt()


firms_ingest_nrt()
