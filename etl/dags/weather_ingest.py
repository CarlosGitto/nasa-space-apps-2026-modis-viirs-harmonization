"""
## Weather ingest — NASA Earthdata (dlt)

Loads gridded weather from NASA Earthdata into the bronze tables of the DuckLake
(`lake.weather_raw.*`), subset to the configured area through OPeNDAP:

| Product | Variables | Grid | Bronze table |
|---|---|---|---|
| MERRA-2 `M2T1NXSLV` (hourly) | 2 m temperature and dew point, 10 m wind (u, v) | 0.5° x 0.625° | `merra2_slv_hourly` |
| GPM IMERG daily Final (`GPM_3IMERGDF`) | precipitation | 0.1° | `imerg_final_daily` |
| GPM IMERG daily Late (`GPM_3IMERGDL`) | precipitation | 0.1° | `imerg_late_daily` |

**Every run loads everything missing**, in chunks: `plan` lists, per product,
the days from the day after the last one loaded (or `WEATHER_DEFAULT_START`,
2025-01-01) up to the last day available, split into `chunk_days` pieces; then
one `load` task per chunk (dynamic task mapping). Each chunk commits on its own,
so a long backfill shows up month by month, a failure retries only its chunk,
and other lake writers get the single-writer pool between chunks.

**Weather follows the fires** (`WEATHER_KEEP=fires`, the default): each chunk
keeps only the grid cells near the fire hexagons of the same dates (see
`include/weather/fires.py`), so the whole world fits in a few GB; and only days
whose fires, loaded with the same `area`, are already in gold get planned,
without gaps: weather waits for FIRMS and catches up on the next run.
`WEATHER_KEEP=all` keeps every cell of the area.

Days not published yet are skipped: MERRA-2 lags ~3–6 weeks (the gap is
accepted), IMERG Late ~14 hours, IMERG Final months. IMERG: Final wherever NASA
has published it; Late only after its last published day (and, in a backfill,
for the days Final doesn't have). Silver prefers Final.

**Backfill / other area:** set `start_date` (and `end_date`) and `area`, e.g.
the whole world from 2025: `{"area": "world", "start_date": "2025-01-01"}`.
Area follows `WEATHER_AREA`, default `FIRMS_AREA`: change fire and weather
together (data/README.md, "Changing the region or the dates").

Append-only: reloading a range adds a new version; dbt silver keeps the latest.
Each successful chunk updates the `weather_raw` asset, which triggers `weather_dbt`.

Needs the Airflow connection `earthdata_default` (Earthdata Login username and
password, or a token as the password) and the GES DISC and OPeNDAP applications
approved in the Earthdata profile (README, "NASA Earthdata Login").
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
                          description="Backfill: last day (empty = everything available)"),
        "chunk_days": Param(31, type="integer", minimum=1, maximum=92,
                            description="Days per load task (one task per chunk)"),
    },
    tags=["weather", "earthdata", "dlt", "ingest"],
)
def weather_ingest():
    @task
    def plan(params=None) -> list[dict]:
        """Chunks to load: [{product, start, end, label}], every product, oldest first."""
        from datetime import date

        from include.firms.config import last_loaded_dates
        from include.weather.config import WEATHER_DEFAULT_START, WEATHER_RAW_DATASET
        from include.weather.earthdata import latest_granule_day
        from include.weather.fires import WEATHER_KEEP, covered_until, fire_days
        from include.weather.source import IMERG_FINAL, IMERG_LATE, MERRA2_SLV, chunk_range, plan_range

        yesterday = pendulum.now("UTC").date() - timedelta(days=1)
        forced_start = date.fromisoformat(params["start_date"]) if params["start_date"] else None
        forced_end = date.fromisoformat(params["end_date"]) if params["end_date"] else None
        until = yesterday
        fires = None
        if WEATHER_KEEP == "fires":
            fires = fire_days(params["area"])
            if not fires:
                print(f"No fires in gold loaded for area {params['area']!r} yet: weather waits for them")
                return []
        last = last_loaded_dates([p.table for p in (MERRA2_SLV, IMERG_FINAL, IMERG_LATE)],
                                 dataset=WEATHER_RAW_DATASET, date_column="obs_date")
        # Each product goes as far as NASA has published it (CMR), never past `until`.
        published = {p: latest_granule_day(p.short_name, p.version) for p in (MERRA2_SLV, IMERG_FINAL, IMERG_LATE)}
        cap = lambda p: min(until, published[p]) if published[p] else None  # noqa: E731
        final_published = published[IMERG_FINAL]

        def product_range(p, last_loaded, first=None):
            if cap(p) is None:
                return None
            start = max(forced_start, first) if forced_start and first else forced_start
            return plan_range(last_loaded, WEATHER_DEFAULT_START, cap(p),
                              start, min(forced_end, cap(p)) if forced_end else None)

        ranges = {
            MERRA2_SLV: product_range(MERRA2_SLV, last.get(MERRA2_SLV.table)),
            IMERG_FINAL: product_range(IMERG_FINAL, last.get(IMERG_FINAL.table)),
            # Late only after Final's last published day, in a backfill too (the load
            # still drops any day Final has).
            IMERG_LATE: product_range(
                IMERG_LATE,
                max([d for d in (last.get(IMERG_LATE.table), last.get(IMERG_FINAL.table), final_published) if d],
                    default=None),
                first=final_published + timedelta(days=1) if final_published else None,
            ),
        }
        if fires is not None:
            # Only days whose fires (same area) are already in gold, without gaps.
            for product, days in ranges.items():
                if days:
                    covered = covered_until(fires, days[0])
                    ranges[product] = (days[0], min(days[1], covered)) if covered else None
                    if covered is None or covered < days[1]:
                        print(f"{product.collection}: fires of {params['area']!r} only cover up to {covered}")
        chunks = []
        for product, days in ranges.items():
            print(f"{product.collection}: {days or 'nothing to load'}")
            for start, end in chunk_range(*days, int(params["chunk_days"])) if days else []:
                chunks.append({"product": product.table, "start": start.isoformat(), "end": end.isoformat(),
                               "label": f"{product.table} {start}..{end}"})
        return chunks

    # One writer at a time on the DuckLake catalog: chunks run one after the other,
    # and other writers (FIRMS loads, dbt) get the pool between them.
    @task(outlets=[WEATHER_RAW], pool=LAKE_WRITER_POOL, map_index_template="{{ chunk_label }}")
    def load(chunk: dict, params=None):
        import os
        from datetime import date

        from airflow.sdk import get_current_context

        from include.firms.config import raw_pipeline
        from include.weather.config import WEATHER_RAW_DATASET, get_earthdata_session
        from include.weather.earthdata import search_granules
        from include.weather.fires import WEATHER_KEEP, fire_zone, near_fire_filter
        from include.weather.source import IMERG_FINAL, IMERG_LATE, PRODUCTS, weather_source

        get_current_context()["chunk_label"] = chunk["label"]
        product = next(p for p in PRODUCTS if p.table == chunk["product"])
        start, end = date.fromisoformat(chunk["start"]), date.fromisoformat(chunk["end"])

        # Skipped tasks don't update the asset, so weather_dbt isn't triggered for nothing.
        from airflow.sdk.exceptions import AirflowSkipException

        keep = None
        if WEATHER_KEEP == "fires":
            zone = fire_zone(start, end)
            print(f"Fire zone of {start}..{end}: {len(zone)} H3 cells")
            if not zone:
                raise AirflowSkipException("No fire detections in these dates: no weather to keep")
            keep = near_fire_filter(zone)

        granules = search_granules(product.short_name, product.version, start, end)
        if product is IMERG_LATE:  # never Late for a day Final has
            final_days = {g.day for g in search_granules(IMERG_FINAL.short_name, IMERG_FINAL.version, start, end)}
            granules = [g for g in granules if g.day not in final_days]
        print(f"{chunk['label']}: {len(granules)} granules published")
        if not granules:
            raise AirflowSkipException("Nothing published for this chunk")

        # dlt doesn't add _dlt_load_id to Arrow tables by default; silver's
        # incremental logic and dedup rely on it, as for FIRMS.
        os.environ["NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID"] = "true"
        pipeline = raw_pipeline(f"weather_{product.table}", dataset=WEATHER_RAW_DATASET)
        load_info = pipeline.run(
            weather_source(session=get_earthdata_session(), plan={product: granules}, area=params["area"], keep=keep)
        )
        print(load_info)
        print(pipeline.last_trace.last_normalize_info)

    load.expand(chunk=plan())


weather_ingest()
