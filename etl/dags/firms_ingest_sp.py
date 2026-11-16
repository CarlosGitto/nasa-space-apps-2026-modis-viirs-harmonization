"""
## FIRMS ingest — Standard Processing / historical (dlt)

Loads MODIS and VIIRS **standard (science-quality)** active fire detections
from the [FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) in
the bronze tables of the DuckLake (`lake.firms_raw.*_sp`, one table per
product). SP data is published with a lag of a few months and replaces NRT as
the reference record. Append-only: every load is kept as a new version; dbt
silver deduplicates.

**Every run loads everything missing**, in chunks. `plan` works out, per product:
- `start_date` param set → from there (manual backfill of any range, e.g. another area).
- otherwise → the day after the last `acq_date` already loaded (or
  `SP_DEFAULT_START`, 2025-01-01, if the table is empty).
- up to `end_date` if set, else everything FIRMS reports as available.

and splits that into `chunk_days` pieces; then one `load` task per chunk
(dynamic task mapping). Each chunk commits on its own (a long backfill shows
up month by month, a failure retries only its chunk) and releases the
single-writer pool, so dbt and the weather loads can run in between. Inside a
chunk, requests are 5 days each (the API maximum).

On success each chunk updates the `firms_raw_sp` asset, which triggers `firms_dbt_sp`.

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
                          description="Empty = everything FIRMS has published"),
        "chunk_days": Param(30, type="integer", minimum=5, maximum=92,
                            description="Days per load task (one task per chunk)"),
    },
    tags=["firms", "sp", "dlt", "ingest"],
)
def firms_ingest_sp():
    @task
    def plan(params=None) -> list[dict]:
        """Chunks to load: [{label, ranges: {product: [start, end]}}], oldest first."""
        from include.firms.config import get_map_key, last_loaded_dates
        from include.firms.source import SP_PRODUCTS, fetch_availability
        from include.weather.source import chunk_range

        yesterday = pendulum.now("UTC").date() - timedelta(days=1)
        forced_start = date.fromisoformat(params["start_date"]) if params["start_date"] else None
        forced_end = date.fromisoformat(params["end_date"]) if params["end_date"] else None
        availability = fetch_availability(get_map_key())
        last_loaded = last_loaded_dates([p.lower() for p in SP_PRODUCTS])

        ranges = {}
        for product in SP_PRODUCTS:
            if forced_start:
                start = forced_start
            elif product.lower() in last_loaded:
                start = last_loaded[product.lower()] + timedelta(days=1)
            else:
                start = SP_DEFAULT_START
            end = forced_end or yesterday
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
            print(f"{product}: {start} .. {end}")
        if not ranges:
            return []

        # One window over all products, cut in chunks; each chunk asks every product
        # for its part of the window.
        chunks = []
        first, last = min(s for s, _ in ranges.values()), max(e for _, e in ranges.values())
        for lo, hi in chunk_range(first, last, int(params["chunk_days"])):
            part = {p: [max(lo, s).isoformat(), min(hi, e).isoformat()]
                    for p, (s, e) in ranges.items() if s <= hi and e >= lo}
            if part:
                chunks.append({"label": f"{lo}..{hi}", "ranges": part})
        print(f"{len(chunks)} chunks")
        return chunks

    # The load writes to the DuckLake catalog: one writer at a time, chunk by chunk.
    @task(outlets=[FIRMS_RAW_SP], pool=LAKE_WRITER_POOL, map_index_template="{{ chunk_label }}")
    def load(chunk: dict, params=None):
        from airflow.sdk import get_current_context

        from include.firms.config import get_map_key, raw_pipeline
        from include.firms.source import firms_sp_source

        get_current_context()["chunk_label"] = chunk["label"]
        ranges = {p: (date.fromisoformat(s), date.fromisoformat(e)) for p, (s, e) in chunk["ranges"].items()}
        for product, (s, e) in ranges.items():
            print(f"{product}: loading {s} .. {e}")
        pipeline = raw_pipeline("firms_sp")
        load_info = pipeline.run(firms_sp_source(map_key=get_map_key(), ranges=ranges, area=params["area"]))
        print(load_info)
        print(pipeline.last_trace.last_normalize_info)

    load.expand(chunk=plan())


firms_ingest_sp()
