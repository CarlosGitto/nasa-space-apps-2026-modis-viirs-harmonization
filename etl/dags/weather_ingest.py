"""
## Weather ingest — NASA Earthdata (dlt)

Loads gridded weather from NASA Earthdata into the bronze tables of the DuckLake
(`lake.weather_raw.*`), subset to the configured area through OPeNDAP:

| Task | Product | Variables | Grid |
|---|---|---|---|
| `load_merra2` | MERRA-2 `M2T1NXSLV` (hourly) | 2 m temperature and dew point, 10 m wind (u, v) | 0.5° x 0.625° |
| `load_imerg` | GPM IMERG daily, Final (`GPM_3IMERGDF`) or Late (`GPM_3IMERGDL`) | precipitation | 0.1° |

**How a run picks its dates, per product:**
- `start_date` set → loads that range (manual backfill).
- otherwise → resumes the day after the last day already loaded (or
  `WEATHER_DEFAULT_START` for an empty table), at most `max_days` days.
- only days NASA has published are loaded: MERRA-2 lags ~3–6 weeks (the
  recent gap is accepted for now), IMERG Late ~14 hours, IMERG Final months.
- IMERG: Final wherever it exists; Late only for the days Final doesn't cover
  yet. Final keeps its own resume point, so it backfills on its own when NASA
  publishes it, and silver then prefers it over Late.

Append-only: reloading a range adds a new version; dbt silver keeps the latest.
On success each task updates the `weather_raw` asset, which triggers `weather_dbt`.

**Area and dates follow the FIRMS dev dataset** (`WEATHER_AREA`, default
`FIRMS_AREA`): change both together (data/README.md, "Changing the region or the dates").

Needs the Airflow connection `earthdata_default` (Earthdata Login username and
password, or a token as the password) and the "NASA GESDISC DATA ARCHIVE"
application authorized in the Earthdata profile.
"""

from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from include.firms.config import LAKE_WRITER_POOL
from include.weather.config import WEATHER_AREA, WEATHER_RAW


def _load(plan: dict, area: str, pipeline_name: str) -> None:
    """Run one dlt pipeline over the planned granules (shared by both tasks)."""
    import os

    from include.firms.config import raw_pipeline
    from include.weather.config import WEATHER_RAW_DATASET, get_earthdata_session
    from include.weather.source import weather_source

    if not any(plan.values()):
        # Skipped tasks don't update the asset, so weather_dbt isn't triggered for nothing.
        from airflow.sdk.exceptions import AirflowSkipException

        raise AirflowSkipException("Nothing new published for this range")

    # dlt doesn't add _dlt_load_id to Arrow tables by default; silver's
    # incremental logic and dedup rely on it, as for FIRMS.
    os.environ["NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID"] = "true"
    pipeline = raw_pipeline(pipeline_name, dataset=WEATHER_RAW_DATASET)
    load_info = pipeline.run(weather_source(session=get_earthdata_session(), plan=plan, area=area))
    print(load_info)
    print(pipeline.last_trace.last_normalize_info)


def _dates(params):
    from datetime import date

    yesterday = pendulum.now("UTC").date() - timedelta(days=1)
    start = date.fromisoformat(params["start_date"]) if params["start_date"] else None
    end = date.fromisoformat(params["end_date"]) if params["end_date"] else None
    return yesterday, start, end, int(params["max_days"])


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
                          description="Backfill: last day (empty = start + max_days, at most yesterday)"),
        "max_days": Param(31, type="integer", minimum=1, maximum=366,
                          description="Days per product per run when end_date is empty"),
    },
    tags=["weather", "earthdata", "dlt", "ingest"],
)
def weather_ingest():
    # Both tasks write to the DuckLake catalog: one writer at a time.
    @task(outlets=[WEATHER_RAW], pool=LAKE_WRITER_POOL)
    def load_merra2(params=None):
        from include.firms.config import last_loaded_dates
        from include.weather.config import WEATHER_DEFAULT_START, WEATHER_RAW_DATASET
        from include.weather.earthdata import search_granules
        from include.weather.source import MERRA2_SLV, plan_range

        yesterday, forced_start, forced_end, max_days = _dates(params)
        last = last_loaded_dates([MERRA2_SLV.table], dataset=WEATHER_RAW_DATASET, date_column="obs_date")
        days = plan_range(last.get(MERRA2_SLV.table), WEATHER_DEFAULT_START, yesterday, max_days,
                          forced_start, forced_end)
        granules = search_granules(MERRA2_SLV.short_name, MERRA2_SLV.version, *days) if days else []
        print(f"{MERRA2_SLV.collection}: range {days}, {len(granules)} granules published")
        _load({MERRA2_SLV: granules}, params["area"], "weather_merra2")

    @task(outlets=[WEATHER_RAW], pool=LAKE_WRITER_POOL)
    def load_imerg(params=None):
        from include.firms.config import last_loaded_dates
        from include.weather.config import WEATHER_DEFAULT_START, WEATHER_RAW_DATASET
        from include.weather.earthdata import latest_granule_day, search_granules
        from include.weather.source import IMERG_FINAL, IMERG_LATE, plan_range

        yesterday, forced_start, forced_end, max_days = _dates(params)
        last = last_loaded_dates([IMERG_FINAL.table, IMERG_LATE.table],
                                 dataset=WEATHER_RAW_DATASET, date_column="obs_date")

        # Final: its own resume point, so it catches up whenever NASA publishes it.
        final_days = plan_range(last.get(IMERG_FINAL.table), WEATHER_DEFAULT_START, yesterday, max_days,
                                forced_start, forced_end)
        final = search_granules(IMERG_FINAL.short_name, IMERG_FINAL.version, *final_days) if final_days else []
        covered = {g.day for g in final}

        # Late: only after the last day NASA has published Final, not just what this run
        # loaded (max_days may stop Final short of it): Late for a day Final already has
        # would be downloaded only to be replaced by Final on the next run.
        final_published = latest_granule_day(IMERG_FINAL.short_name, IMERG_FINAL.version)
        late_after = max([d for d in (last.get(IMERG_LATE.table), last.get(IMERG_FINAL.table),
                                      max(covered, default=None), final_published) if d], default=None)
        late_days = plan_range(late_after, WEATHER_DEFAULT_START, yesterday, max_days, forced_start, forced_end)
        late = search_granules(IMERG_LATE.short_name, IMERG_LATE.version, *late_days) if late_days else []
        late = [g for g in late if g.day not in covered]

        print(f"{IMERG_FINAL.collection}: range {final_days}, {len(final)} granules published")
        print(f"{IMERG_LATE.collection}: range {late_days}, {len(late)} granules for days Final doesn't cover")
        _load({IMERG_FINAL: final, IMERG_LATE: late}, params["area"], "weather_imerg")

    load_merra2()
    load_imerg()


weather_ingest()
