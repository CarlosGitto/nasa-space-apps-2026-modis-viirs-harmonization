"""dlt source for the map's weather: NASA POWER daily values around fires.

| Bronze table (lake.weather_raw) | Source                     | Grid          | Time step |
|---------------------------------|----------------------------|---------------|-----------|
| power_daily                     | NASA POWER daily regional  | 0.5° x 0.625° | 1 UTC day |

Columns: obs_date, lat, lon (cell center), t2m_max (°C), precipitation (mm),
ws10m (m/s, daily mean), wd10m (degrees, where the wind comes from), plus lineage
(_box, _area, _source, _extracted_at). Append-only like the FIRMS bronze tables:
reloading a day adds a new version (new _dlt_load_id), silver keeps the latest.

Only the cells near fires are requested and kept (fires.py); see power.py for the
API and its limits. The hexagon panel doesn't use this table: the backend asks
POWER for one point on demand.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, timedelta

import dlt

logger = logging.getLogger(__name__)

TABLE = "power_daily"


def plan_range(
    last_loaded: date | None,
    default_start: date,
    until: date,
    forced_start: date | None = None,
    forced_end: date | None = None,
) -> tuple[date, date] | None:
    """Days a run loads, or None if there is nothing to do.

    Forced dates (a manual backfill) win. Otherwise everything from the day after
    the last day loaded (or default_start for an empty table) up to `until`. The
    DAG splits it into chunks (chunk_range), one task each.
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


def unique_cells(table):
    """One row per (obs_date, lat, lon): POWER boxes include both edges, so two boxes
    that share an edge both return its cells."""
    import duckdb

    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")  # keep _extracted_at in UTC
    con.register("t", table)
    return con.execute("select * from t qualify row_number() over (partition by obs_date, lat, lon) = 1").fetch_arrow_table()


@dlt.source(name="weather")
def weather_source(boxes: list[tuple[float, float, float, float]], start: date, end: date, area: str, keep=None):
    """POWER daily values for every box (<= 10° a side) and day in [start, end].

    keep: optional Arrow table -> Arrow table filter (the exact cells near fires).
    """
    from include.weather.power import fetch_box

    @dlt.resource(name=TABLE, write_disposition="append")
    def cells() -> Iterator:
        import pyarrow as pa

        parts = []
        for i, box in enumerate(boxes, 1):
            part = fetch_box(box, start, end, area)
            logger.info("box %s/%s %s: %s cell-days", i, len(boxes), box, part.num_rows)
            if part.num_rows:
                parts.append(part)
        if not parts:
            return
        table = unique_cells(pa.concat_tables(parts))
        total = table.num_rows
        if keep is not None:
            table = keep(table)
        logger.info("%s..%s: %s of %s cell-days kept (near fires)", start, end, table.num_rows, total)
        if table.num_rows:
            yield table

    return cells
