"""
## Weather ingest — NASA POWER (dlt)

Loads the daily weather the map shows around fires into the bronze table
`lake.weather_raw.power_daily`, from the [NASA POWER](https://power.larc.nasa.gov)
daily regional API: max temperature, total rain and mean wind (speed and
direction) per MERRA-2 grid cell (0.5° x 0.625°) and UTC day. POWER serves
MERRA-2 meteorology (GEOS-IT for the latest days) already aggregated per day,
~2 days behind real time, with no login.

The hexagon panel doesn't depend on this DAG: the backend asks POWER for one
point on demand. This table only feeds the map layers.

**Every run loads everything missing**, in chunks: `plan` lists the days from
the day after the last one loaded (or `WEATHER_DEFAULT_START`, 2025-01-01) to
yesterday, split into `chunk_days` pieces; then one `load` task per chunk
(dynamic task mapping), each committing on its own and releasing the
single-writer pool in between. Days POWER hasn't published yet come back empty
and are asked again the next day.

**Only around fires**: each chunk takes the fire hexagons of its dates from gold,
turns them into boxes of at most 10° (the API limit; `include/weather/fires.py`)
and downloads only those, one parameter per request (the API limit), one request
at a time with a pause and back-off on HTTP 429 (`include/weather/power.py`); it
then keeps the exact cells near the fires. Only days whose fires, loaded with
the same `area`, are already in gold get planned, without gaps: weather waits
for FIRMS and catches up on the next run. `WEATHER_KEEP=all` keeps every cell
of the area (boxes still at most 10°).

**Backfill / other area:** set `start_date` (and `end_date`) and `area`, e.g. the
whole world from 2025: `{"area": "world", "start_date": "2025-01-01"}`. Area
follows `WEATHER_AREA`, default `FIRMS_AREA`.

Each successful chunk updates the `weather_raw` asset, which triggers `weather_dbt`.
"""

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import LAKE_WRITER_POOL
from include.weather.config import WEATHER_AREA, WEATHER_RAW


@dag(
    start_date=pendulum.datetime(2026, 11, 1, tz="UTC"),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    doc_md=__doc__,
    default_args={"owner": "etl", "retries": 2, "retry_delay": timedelta(minutes=10)},
    params={
        "area": Param(WEATHER_AREA, type="string",
                      description='"world" or "west,south,east,north" (default: WEATHER_AREA, else FIRMS_AREA)'),
        "start_date": Param(None, type=["null", "string"], format="date",
                            description="Backfill: first day (empty = resume after the last loaded day)"),
        "end_date": Param(None, type=["null", "string"], format="date",
                          description="Backfill: last day (empty = yesterday)"),
        "chunk_days": Param(31, type="integer", minimum=1, maximum=92,
                            description="Days per load task (one task per chunk)"),
    },
    tags=["weather", "power", "dlt", "ingest"],
)
def weather_ingest():
    @task
    def plan(params=None) -> list[dict]:
        """Chunks to load: [{start, end, label}], oldest first."""
        from datetime import date

        from include.firms.config import last_loaded_dates
        from include.weather.config import WEATHER_DEFAULT_START, WEATHER_RAW_DATASET
        from include.weather.fires import WEATHER_KEEP, covered_until, fire_days
        from include.weather.source import TABLE, chunk_range, plan_range

        yesterday = pendulum.now("UTC").date() - timedelta(days=1)
        forced_start = date.fromisoformat(params["start_date"]) if params["start_date"] else None
        forced_end = date.fromisoformat(params["end_date"]) if params["end_date"] else None
        last = last_loaded_dates([TABLE], dataset=WEATHER_RAW_DATASET, date_column="obs_date").get(TABLE)
        days = plan_range(last, WEATHER_DEFAULT_START, yesterday, forced_start, forced_end)

        if days and WEATHER_KEEP == "fires":
            fires = fire_days(params["area"])
            covered = covered_until(fires, days[0]) if fires else None
            if covered is None:
                print(f"No fires in gold loaded for area {params['area']!r} from {days[0]}: weather waits for them")
                return []
            if covered < days[1]:
                print(f"Fires of {params['area']!r} only cover up to {covered}")
            days = (days[0], min(days[1], covered))
        print(f"POWER daily: {days or 'nothing to load'}")
        return [{"start": s.isoformat(), "end": e.isoformat(), "label": f"{s}..{e}"}
                for s, e in (chunk_range(*days, int(params["chunk_days"])) if days else [])]

    # One writer at a time on the DuckLake catalog: chunks run one after the other,
    # and other writers (FIRMS loads, dbt) get the pool between them.
    @task(outlets=[WEATHER_RAW], pool=LAKE_WRITER_POOL, map_index_template="{{ chunk_label }}")
    def load(chunk: dict, params=None):
        import os
        from datetime import date

        from airflow.sdk import get_current_context
        from airflow.sdk.exceptions import AirflowSkipException

        from include.firms.config import raw_pipeline
        from include.weather.config import WEATHER_RAW_DATASET
        from include.weather.fires import WEATHER_KEEP, fire_zone, near_fire_filter, zone_boxes, zone_centers
        from include.weather.power import Area, split_box
        from include.weather.source import weather_source

        get_current_context()["chunk_label"] = chunk["label"]
        start, end = date.fromisoformat(chunk["start"]), date.fromisoformat(chunk["end"])
        area = Area.parse(params["area"])

        keep = None
        if WEATHER_KEEP == "fires":
            zone = fire_zone(start, end)
            if not zone:
                # Skipped tasks don't update the asset, so weather_dbt isn't triggered for nothing.
                raise AirflowSkipException("No fire detections in these dates: no weather to keep")
            boxes = zone_boxes(zone_centers(zone))
            keep = near_fire_filter(zone)
        else:
            boxes = [area.box()]
        # POWER takes boxes of at most 10° a side; clip them to the area first.
        boxes = [piece for box in area.clip(boxes) for piece in split_box(box)]
        print(f"{chunk['label']}: {len(boxes)} boxes of <= 10° ({4 * len(boxes)} POWER requests), "
              f"{100 * sum((e - w) * (n - s) for w, s, e, n in boxes) / 64800:.1f} % of the globe")

        # dlt doesn't add _dlt_load_id to Arrow tables by default; silver's
        # incremental logic and dedup rely on it, as for FIRMS.
        os.environ["NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID"] = "true"
        pipeline = raw_pipeline("weather_power", dataset=WEATHER_RAW_DATASET)
        load_info = pipeline.run(weather_source(boxes=boxes, start=start, end=end, area=params["area"], keep=keep))
        print(load_info)
        print(pipeline.last_trace.last_normalize_info)

    load.expand(chunk=plan())


weather_ingest()
