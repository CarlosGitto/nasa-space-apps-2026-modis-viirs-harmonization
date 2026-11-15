"""dlt sources for NASA Earthdata weather: MERRA-2 (wind, temperature, humidity)
and GPM IMERG (precipitation), subset to an area through OPeNDAP.

| Bronze table (lake.weather_raw) | Collection            | Grid             | Time step |
|---------------------------------|-----------------------|------------------|-----------|
| merra2_slv_hourly               | M2T1NXSLV 5.12.4      | 0.5° x 0.625°    | 1 h (mean over the hour, stamped at HH:30) |
| imerg_final_daily               | GPM_3IMERGDF 07       | 0.1° x 0.1°      | 1 day (UTC) |
| imerg_late_daily                | GPM_3IMERGDL 07       | 0.1° x 0.1°      | 1 day (UTC) |

IMERG Final is the research-grade run (published ~3.5 months late, or later);
Late is near real time (~14 h). Silver prefers Final for a day when both exist,
the same way FIRMS SP replaces NRT.

One row per grid cell and time step, the file's values untouched (Kelvin, m/s,
mm): units, derived variables (relative humidity, VPD, wind speed) and
deduplication happen in dbt silver. Append-only, like the FIRMS bronze tables:
reloading a day adds a new version (new _dlt_load_id), silver keeps the latest.

Lineage columns: _granule (file name), _collection, _area, _extracted_at.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import dlt

from include.weather.earthdata import Area, Granule, OpendapReader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Product:
    table: str  # bronze table in lake.weather_raw
    short_name: str  # CMR collection
    version: str
    variables: tuple[tuple[str, str], ...]  # (variable in the file, bronze column); a tuple keeps it hashable
    # One time step per file: the day comes from CMR, not from the file's time axis.
    # IMERG declares a "julian" calendar there (a known quirk of the product), which
    # would need cftime and shift dates by 13 days against the Gregorian calendar.
    daily: bool = False

    @property
    def collection(self) -> str:
        return f"{self.short_name}.{self.version}"

    @property
    def file_variables(self) -> list[str]:
        return [v for v, _ in self.variables]

    @property
    def columns(self) -> dict[str, str]:
        return dict(self.variables)


# T2M / T2MDEW: 2 m air and dew point temperature (K). U10M / V10M: 10 m wind,
# eastward / northward (m/s). Precipitation is the daily total (mm).
MERRA2_SLV = Product(
    "merra2_slv_hourly", "M2T1NXSLV", "5.12.4",
    (("T2M", "t2m"), ("T2MDEW", "t2mdew"), ("U10M", "u10m"), ("V10M", "v10m")),
)
IMERG_FINAL = Product("imerg_final_daily", "GPM_3IMERGDF", "07", (("precipitation", "precipitation"),), daily=True)
IMERG_LATE = Product("imerg_late_daily", "GPM_3IMERGDL", "07", (("precipitation", "precipitation"),), daily=True)

# IMERG Final first: when both cover a day, only Final is downloaded.
PRODUCTS = (MERRA2_SLV, IMERG_FINAL, IMERG_LATE)


def plan_range(
    last_loaded: date | None,
    default_start: date,
    until: date,
    forced_start: date | None = None,
    forced_end: date | None = None,
) -> tuple[date, date] | None:
    """Days a run loads for one product, or None if there is nothing to do.

    Forced dates (a manual backfill) win. Otherwise everything from the day after
    the last day loaded (or default_start for an empty table) up to `until`
    (yesterday, or the last day something it depends on has). The DAGs split it
    into chunks (chunk_range), one task each. Days not published yet are dropped
    later: CMR only lists published granules.
    """
    if forced_start:
        start = forced_start
    elif last_loaded:
        start = last_loaded + timedelta(days=1)
    else:
        start = default_start
    end = forced_end or until
    return (start, end) if start <= end else None


def chunk_range(start: date, end: date, days: int) -> list[tuple[date, date]]:
    """[start, end] split into consecutive pieces of at most `days` days."""
    out = []
    while start <= end:
        last = min(end, start + timedelta(days=days - 1))
        out.append((start, last))
        start = last + timedelta(days=1)
    return out


def to_arrow(ds, product: Product, granule: Granule, area: str):
    """One granule subset (xarray Dataset) -> Arrow table, one row per cell and time step."""
    import pandas as pd
    import pyarrow as pa

    df = ds[product.file_variables].to_dataframe().reset_index()
    df = df.rename(columns={"time": "time_utc", **product.columns})
    if product.daily:
        df["time_utc"] = pd.Timestamp(granule.start).tz_convert(UTC)
    else:
        df["time_utc"] = pd.to_datetime(df["time_utc"]).dt.tz_localize(UTC)
    df["obs_date"] = df["time_utc"].dt.date
    df["_granule"] = granule.title
    df["_collection"] = product.collection
    df["_area"] = area
    df["_extracted_at"] = datetime.now(UTC)
    columns = ["obs_date", "time_utc", "lat", "lon", *product.columns.values(),
               "_granule", "_collection", "_area", "_extracted_at"]
    return pa.Table.from_pandas(df[columns], preserve_index=False)


def _granule_resource(product: Product, reader: OpendapReader, granules: list[Granule], area: str, keep=None):
    @dlt.resource(name=product.table, write_disposition="append")
    def cells() -> Iterator:
        for granule in granules:
            table = to_arrow(reader.read(granule), product, granule, area)
            total = table.num_rows
            if keep is not None:
                table = keep(table)
            logger.info("%s %s: %s of %s cells kept", product.table, granule.day, table.num_rows, total)
            if table.num_rows:
                yield table

    return cells


@dlt.source(name="weather")
def weather_source(session, plan: dict[Product, list[Granule]], area: str = "world", keep=None):
    """One resource per product with granules to load.

    session: authenticated Earthdata session. plan: product -> granules (from
    CMR). area: "world" or "west,south,east,north", like FIRMS. keep: optional
    Arrow table -> Arrow table filter applied to every granule before it's
    written (the cells near fires, see fires.py).
    """
    parsed = Area.parse(area)
    return [
        _granule_resource(
            product,
            OpendapReader(session, product.file_variables, parsed, decode_times=not product.daily),
            granules,
            area,
            keep,
        )
        for product, granules in plan.items()
        if granules
    ]
