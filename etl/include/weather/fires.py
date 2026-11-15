"""Weather only where there is fire: which grid cells to keep.

Weather explains fires, and the map only shows it around them; a global grid every
day of the whole world is ~200 k cells, and most are nowhere near a fire.
So each chunk of weather keeps only the cells near the fires of the same dates:

    fire hexagons (gold, res 6, any detection in [start, end])
      -> their res-FIRE_ZONE_RES parents (~12,000 km² at res 3)
      -> plus FIRE_ZONE_RING rings of neighbors (~100-150 km around each fire)
    a weather cell is kept when its center falls in one of those cells.

Fire data must be there first, and for the same area: the weather DAG only plans
days that gold already has fire detections for, loaded with the same area
(`_firms_area`), walking forward from each product's start and stopping at the
first gap (covered_until). Fires and weather have different ranges (FIRMS SP lags
months and NRT covers the last ones, POWER ~2 days), and a
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
    # UTC session: DuckDB returns timestamps with time zone in the session's zone (the
    # machine's by default). Same instant either way, but bronze stays in UTC.
    con.execute("SET TimeZone = 'UTC'; INSTALL h3 FROM community; LOAD h3;")
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


# ------------------------------------------------------------ what to download ---
# POWER returns rectangles of the grid, so the fire zone is turned into a few boxes
# and only those are requested: the download, not just what's stored, follows the fires.

ZONE_TILE_DEG = 10  # boxes are built from tiles of this size
ZONE_MARGIN_DEG = 1.0  # a zone cell's center can be ~0.7° from its edge: tiles that close count too


def zone_centers(zone: list[int]) -> list[tuple[float, float]]:
    """(lat, lon) of each res-FIRE_ZONE_RES cell of a fire zone."""
    import duckdb
    import pyarrow as pa

    if not zone:
        return []
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    con.register("zone", pa.table({"cell": pa.array(zone, type=pa.uint64())}))
    return con.execute("select h3_cell_to_lat(cell), h3_cell_to_lng(cell) from zone").fetchall()


def zone_boxes(centers: list[tuple[float, float]], tile: float = ZONE_TILE_DEG,
               margin: float = ZONE_MARGIN_DEG) -> list[tuple[float, float, float, float]]:
    """Boxes (west, south, east, north) covering a fire zone, to request from POWER
    (split to <= 10° a side before asking).

    Every tile a zone cell's center falls in, or within `margin` of, is needed; the
    needed tiles of each row are merged into runs of contiguous tiles, one box each.
    """
    import math

    rows = math.ceil(180 / tile)
    cols = math.ceil(360 / tile)
    needed: set[tuple[int, int]] = set()
    for lat, lon in centers:
        for dlat in (-margin, 0, margin):
            for dlon in (-margin, 0, margin):
                r = min(rows - 1, max(0, int((lat + dlat + 90) // tile)))
                c = min(cols - 1, max(0, int((lon + dlon + 180) // tile)))
                needed.add((r, c))
    boxes = []
    for r in sorted({r for r, _ in needed}):
        row = sorted(c for rr, c in needed if rr == r)
        start = prev = row[0]
        for c in [*row[1:], None]:
            if c is not None and c == prev + 1:
                prev = c
                continue
            south = -90 + r * tile
            boxes.append((-180 + start * tile, south, min(180, -180 + (prev + 1) * tile), min(90, south + tile)))
            if c is not None:
                start = prev = c
    return boxes
