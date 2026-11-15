"""NASA POWER on demand: the daily weather of one point, for the hexagon panel.

POWER (https://power.larc.nasa.gov) serves MERRA-2 meteorology (GEOS-IT for the latest
days) already aggregated per day, on the MERRA-2 grid (0.5° x 0.625°), ~2 days behind
real time, from 1981, with no login. One point request returns up to 20 parameters
for any date range: ~1.5 s for a month.

Usage limits (https://power.larc.nasa.gov/docs/services/api/temporal/daily/): no
published rate, only HTTP 429 and the warning that an application "requesting the same
relative location ... will potentially be blocked". So:
- A persistent cache (SQLite, POWER_CACHE_PATH) keyed by POWER grid cell and day:
  every hexagon of the same 0.5° x 0.625° cell, and every later visit, reuses it.
  Days older than RECENT_DAYS are kept for good; recent ones (POWER fills them in late)
  are asked again at most every RECENT_TTL.
- One request at a time, MIN_INTERVAL_S apart; on 429/5xx one retry, then
  PowerUnavailable (the API answers 503) instead of insisting.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

POWER_POINT_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
COMMUNITY = "AG"
FILL_VALUE = -999.0
# POWER parameter -> our name. One request takes up to 20.
PARAMETERS = {
    "T2M_MAX": "t2m_max_c",
    "T2M_MIN": "t2m_min_c",
    "T2M": "t2m_mean_c",
    "T2MDEW": "dewpoint_mean_c",
    "RH2M": "rh_mean_pct",
    "WS10M": "wind_speed_mean_ms",
    "WS10M_MAX": "wind_speed_max_ms",
    "WD10M": "wind_dir_mean_deg",
    "PRECTOTCORR": "precipitation_mm",
}
GRID = (0.5, 0.625)  # MERRA-2 cell size (lat, lon): POWER answers per cell
FIRST_DAY = date(1981, 1, 1)
RECENT_DAYS = 10
RECENT_TTL = timedelta(hours=6)
MIN_INTERVAL_S = 1.0
TIMEOUT_S = 60

REPO_ROOT = Path(__file__).resolve().parents[2]


class PowerUnavailable(RuntimeError):
    """POWER didn't answer (rate limited, down, timed out)."""


def cell_of(lat: float, lon: float) -> tuple[float, float]:
    """Center of the POWER (MERRA-2) grid cell a point falls in."""
    return round(round(lat / GRID[0]) * GRID[0], 4), round(round(lon / GRID[1]) * GRID[1], 4)


def _cache_path() -> str:
    return os.environ.get("POWER_CACHE_PATH", str(REPO_ROOT / "data" / "cache" / "power.sqlite"))


_lock = threading.Lock()  # one POWER request at a time, and one writer on the cache
_last_request = 0.0


def _db() -> sqlite3.Connection:
    path = _cache_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "create table if not exists power_daily ("
        " cell_lat real, cell_lon real, day text, values_json text, fetched_at text,"
        " primary key (cell_lat, cell_lon, day))"
    )
    return con


def _fetch(lat: float, lon: float, start: date, end: date) -> dict[date, dict]:
    """One POWER point request: {day: {our name: value or None}} for [start, end]."""
    global _last_request
    query = urllib.parse.urlencode({
        "parameters": ",".join(PARAMETERS), "community": COMMUNITY, "format": "JSON", "time-standard": "UTC",
        "latitude": lat, "longitude": lon, "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
    })
    for attempt in range(2):
        wait = _last_request + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(f"{POWER_POINT_URL}?{query}", timeout=TIMEOUT_S) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                logger.warning("POWER %s, retrying once in 5 s", e.code)
                time.sleep(5)
                continue
            raise PowerUnavailable(f"NASA POWER answered {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise PowerUnavailable(f"NASA POWER unreachable: {e}") from None
    series = payload["properties"]["parameter"]
    out: dict[date, dict] = {}
    for param, name in PARAMETERS.items():
        for day, value in series.get(param, {}).items():
            d = datetime.strptime(day, "%Y%m%d").date()
            out.setdefault(d, {})[name] = None if value is None or value == FILL_VALUE else value
    return out


def daily(lat: float, lon: float, start: date, end: date) -> dict[date, dict]:
    """Daily weather of the POWER cell under (lat, lon) for [start, end]: {day: values}.

    Served from the cache when it has every day (recent ones not older than RECENT_TTL);
    otherwise one request for the missing span, stored for next time.
    """
    cell = cell_of(lat, lon)
    start = max(start, FIRST_DAY)
    if end < start:
        return {}
    now = datetime.now(timezone.utc)
    recent = now.date() - timedelta(days=RECENT_DAYS)
    with _lock:
        con = _db()
        try:
            rows = con.execute(
                "select day, values_json, fetched_at from power_daily "
                "where cell_lat = ? and cell_lon = ? and day between ? and ?",
                [*cell, start.isoformat(), end.isoformat()],
            ).fetchall()
            cached = {date.fromisoformat(d): (json.loads(v), datetime.fromisoformat(f)) for d, v, f in rows}
            missing = [
                d for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
                if d not in cached or (d >= recent and now - cached[d][1] > RECENT_TTL)
            ]
            if missing:
                fetched = _fetch(*cell, min(missing), max(missing))
                con.executemany(
                    "insert or replace into power_daily values (?, ?, ?, ?, ?)",
                    [(*cell, d.isoformat(), json.dumps(v), now.isoformat()) for d, v in fetched.items()],
                )
                con.commit()
                for d, v in fetched.items():
                    cached[d] = (v, now)
        finally:
            con.close()
    return {d: v for d, (v, _) in sorted(cached.items()) if start <= d <= end}


# ------------------------------------------------------- many places at once ---
# A selection of hexagons (or a drawn area) on the map: the four values the map draws
# (max temperature, rain, mean wind speed and direction) for every POWER cell under it.
# Whichever needs fewer requests: one point request per cell (they also fill the
# panel's cache), or POWER's regional endpoint, one request per parameter and box of
# <= 10° a side (the API's limits). A few scattered hexagons go by point; a drawn area
# (tens of cells close together) by region.

MAP_PARAMETERS = {"T2M_MAX": "t2m_max_c", "PRECTOTCORR": "precipitation_mm", "WS10M": "wind_speed_mean_ms",
                  "WD10M": "wind_dir_mean_deg"}
POWER_REGIONAL_URL = "https://power.larc.nasa.gov/api/temporal/daily/regional"
POINT_CELLS = 3
MAX_BOX_DEG = 10


def _get_json(url: str, params: dict) -> dict:
    """GET with the module's pacing: one request at a time, MIN_INTERVAL_S apart, one
    retry on 429/5xx, then PowerUnavailable. Call with _lock held."""
    global _last_request
    query = urllib.parse.urlencode(params)
    for attempt in range(2):
        wait = _last_request + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(f"{url}?{query}", timeout=TIMEOUT_S) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                logger.warning("POWER %s, retrying once in 5 s", e.code)
                time.sleep(5)
                continue
            raise PowerUnavailable(f"NASA POWER answered {e.code}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise PowerUnavailable(f"NASA POWER unreachable: {e}") from None
    raise PowerUnavailable("NASA POWER: no answer")


def _fetch_regional(box: tuple[float, float, float, float], start: date, end: date) -> dict[tuple[float, float], dict[date, dict]]:
    """{cell: {day: map values}} for every cell of one box (<= 10°), one request per parameter."""
    west, south, east, north = box
    out: dict[tuple[float, float], dict[date, dict]] = {}
    for param, name in MAP_PARAMETERS.items():
        payload = _get_json(POWER_REGIONAL_URL, {
            "parameters": param, "community": COMMUNITY, "format": "JSON", "time-standard": "UTC",
            "latitude-min": south, "latitude-max": north, "longitude-min": west, "longitude-max": east,
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
        })
        for feature in payload.get("features", []):
            lon, lat = feature["geometry"]["coordinates"][:2]
            days = out.setdefault((round(lat, 4), round(lon, 4)), {})
            for day, value in feature["properties"]["parameter"][param].items():
                d = datetime.strptime(day, "%Y%m%d").date()
                days.setdefault(d, {})[name] = None if value is None or value == FILL_VALUE else value
    return out


def _boxes(cells: list[tuple[float, float]]) -> list[tuple[float, float, float, float]]:
    """Boxes of <= MAX_BOX_DEG covering the cells (centers +- half a cell)."""
    lats, lons = [c[0] for c in cells], [c[1] for c in cells]
    west, east = min(lons) - GRID[1] / 2, max(lons) + GRID[1] / 2
    south, north = max(-90, min(lats) - GRID[0] / 2), min(90, max(lats) + GRID[0] / 2)
    out, lat = [], south
    while lat < north:
        lon = west
        while lon < east:
            out.append((lon, lat, min(east, lon + MAX_BOX_DEG), min(north, lat + MAX_BOX_DEG)))
            lon += MAX_BOX_DEG
        lat += MAX_BOX_DEG
    return out


def map_values(points: list[tuple[float, float]], start: date, end: date) -> dict[tuple[float, float], dict[date, dict]]:
    """{cell: {day: map values}} for the POWER cells under `points`, [start, end].

    Cached like `daily` (same file, its own table): a cell-day is asked once (recent days
    again after RECENT_TTL).
    """
    cells = sorted({cell_of(lat, lon) for lat, lon in points})
    if not cells or end < start:
        return {}
    by_point = len(cells)
    by_region = len(MAP_PARAMETERS) * len(_boxes(cells))
    if len(cells) <= POINT_CELLS or by_point <= by_region:  # also what the panel will ask for
        return {c: {d: {k: v.get(k) for k in MAP_PARAMETERS.values()} for d, v in daily(*c, start, end).items()}
                for c in cells}
    now = datetime.now(timezone.utc)
    recent = now.date() - timedelta(days=RECENT_DAYS)
    n_days = (end - start).days + 1
    with _lock:
        con = _db()
        try:
            con.execute(
                "create table if not exists power_map_daily ("
                " cell_lat real, cell_lon real, day text, values_json text, fetched_at text,"
                " primary key (cell_lat, cell_lon, day))"
            )
            con.execute("create temp table want (cell_lat real, cell_lon real)")
            con.executemany("insert into want values (?, ?)", cells)
            rows = con.execute(
                "select m.cell_lat, m.cell_lon, m.day, m.values_json, m.fetched_at from power_map_daily m "
                "join want w using (cell_lat, cell_lon) where m.day between ? and ?",
                [start.isoformat(), end.isoformat()],
            ).fetchall()
            out: dict[tuple[float, float], dict[date, dict]] = {}
            fresh: dict[tuple[float, float], int] = {}
            for lat, lon, day, values, fetched in rows:
                d = date.fromisoformat(day)
                out.setdefault((lat, lon), {})[d] = json.loads(values)
                if d < recent or now - datetime.fromisoformat(fetched) <= RECENT_TTL:
                    fresh[(lat, lon)] = fresh.get((lat, lon), 0) + 1
            missing = [c for c in cells if fresh.get(c, 0) < n_days]
            if missing:
                for box in _boxes(missing):
                    got = _fetch_regional(box, start, end)
                    con.executemany(
                        "insert or replace into power_map_daily values (?, ?, ?, ?, ?)",
                        [(*c, d.isoformat(), json.dumps(v), now.isoformat()) for c, days in got.items() for d, v in days.items()],
                    )
                    for c, days in got.items():  # every cell of the box is cached; only `cells` returned
                        out.setdefault(c, {}).update(days)
                con.commit()
        finally:
            con.close()
    return {c: dict(sorted(out.get(c, {}).items())) for c in cells}
