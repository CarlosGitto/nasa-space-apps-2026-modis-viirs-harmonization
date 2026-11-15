"""Weather only where there is fire: which grid cells to keep.

Weather explains fires, and the map only shows it around them; a global grid every
day would be billions of rows (MERRA-2 hourly: ~5 M per day; IMERG daily: ~6.5 M).
So each chunk of weather keeps only the cells near the fires of the same dates:

    fire hexagons (gold, res 6, any detection in [start, end])
      -> their res-FIRE_ZONE_RES parents (~12,000 km² at res 3)
      -> plus FIRE_ZONE_RING rings of neighbors (~100-150 km around each fire)
    a weather cell is kept when its center falls in one of those cells.

Fire data must be there first, and for the same area: the weather DAG only plans
days that gold already has fire detections for, loaded with the same area
(`_firms_area`), walking forward from each product's start and stopping at the
first gap (covered_until). Fires and weather have different ranges (FIRMS SP lags
months and NRT covers the last ones, MERRA-2 lags weeks, IMERG Late a day), and a
backfill of another area (e.g. South America -> world) lands month by month: so
weather for a month never runs with only part of its fires, and catches up on
the next run once FIRMS has filled the gap.

Set WEATHER_KEEP=all in etl/.env to keep every cell of the area instead.
"""

from __future__ import annotations

import os
from datetime import date

FIRE_ZONE_RES = 3
FIRE_ZONE_RING = 1
WEATHER_KEEP = os.getenv("WEATHER_KEEP", "fires")  # "fires" | "all"
FIRE_TABLE = "gold.fct_hexagon_daily"


def _connect():
    import duckdb

    from include.firms.config import attach_sql, catalog_initialized

    if not catalog_initialized():
        return None
    con = duckdb.connect()
    con.execute("LOAD ducklake; LOAD sqlite; INSTALL h3 FROM community; LOAD h3;")
    con.execute(attach_sql(read_only=True))
    return con


def _has_fire_table(con) -> bool:
    return con.execute(
        "select count(*) from information_schema.tables "
        "where table_catalog = 'lake' and table_schema = 'gold' and table_name = 'fct_hexagon_daily'"
    ).fetchone()[0] > 0


MAX_FIRE_GAP_DAYS = 7  # days without detections tolerated inside the covered range


def fire_days(area: str) -> set[date]:
    """Days gold has wildfire detections for that were loaded with `area` (FIRMS format)."""
    con = _connect()
    if con is None or not _has_fire_table(con):
        return set()
    with con:
        rows = con.execute(
            "select distinct acq_date from lake.gold.fct_hexagon_detections "
            "where _firms_area = ? and not is_static_source",
            [area],
        ).fetchall()
    return {r[0] for r in rows}


def covered_until(days: set[date], start: date, max_gap: int = MAX_FIRE_GAP_DAYS) -> date | None:
    """Last day of the run of fire days that starts at `start` (gaps up to max_gap days
    allowed), or None if there are no fire days from `start` within max_gap."""
    from datetime import timedelta

    last, day, gap = None, start, 0
    horizon = max(days, default=start)
    while day <= horizon and gap <= max_gap:
        if day in days:
            last, gap = day, 0
        else:
            gap += 1
        day += timedelta(days=1)
    return last


def fire_zone(start: date, end: date) -> list[int]:
    """H3 res-FIRE_ZONE_RES cells within FIRE_ZONE_RING rings of a fire in [start, end]."""
    con = _connect()
    if con is None or not _has_fire_table(con):
        return []
    with con:
        rows = con.execute(
            f"""
            select distinct unnest(h3_grid_disk(parent, {FIRE_ZONE_RING}))
            from (
                select distinct h3_cell_to_parent(h3_r6, {FIRE_ZONE_RES}) as parent
                from lake.{FIRE_TABLE}
                where acq_date between ? and ? and fire_detections > 0
            )
            """,
            [start, end],
        ).fetchall()
    return [r[0] for r in rows]


def near_fire_filter(zone: list[int]):
    """Arrow table -> the rows whose (lat, lon) fall in `zone` (res-FIRE_ZONE_RES cells)."""
    import duckdb
    import pyarrow as pa

    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    con.register("zone", pa.table({"cell": pa.array(zone, type=pa.uint64())}))

    def keep(table):
        con.register("grid", table)
        try:
            return con.execute(
                f"select grid.* from grid "
                f"semi join zone on zone.cell = h3_latlng_to_cell(grid.lat, grid.lon, {FIRE_ZONE_RES})"
            ).fetch_arrow_table()
        finally:
            con.unregister("grid")

    return keep
