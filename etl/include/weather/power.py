"""NASA POWER: daily weather for the map, downloaded only around fires.

POWER (https://power.larc.nasa.gov) serves MERRA-2 meteorology (GEOS-IT for the most
recent days) already aggregated per day, on the MERRA-2 grid (0.5° x 0.625°), about
2 days behind real time, with no login. Its daily values are the same we used to
compute from the hourly MERRA-2 files (checked: 0.00 °C apart over a month), at a
fraction of the download.

Regional endpoint: one parameter per request, boxes of at most 10° x 10°:
    /api/temporal/daily/regional?parameters=T2M_MAX&latitude-min=..&latitude-max=..
        &longitude-min=..&longitude-max=..&start=YYYYMMDD&end=YYYYMMDD&community=AG
A 10° box, one parameter, one month: ~2 s, ~230 KB of GeoJSON.

Usage limits (https://power.larc.nasa.gov/docs/services/api/temporal/daily/): POWER
publishes no rate numbers, only HTTP 429 "Too Many Requests" and the warning that an
application "requesting the same relative location ... will potentially be blocked".
Regional requests take one parameter, and the API rejects boxes over 10° a side. So:
- one request at a time, at least MIN_INTERVAL_S apart, backing off on 429/5xx;
- boxes of at most 10°, one request per parameter;
- never the same box, parameter and days twice: the DAG resumes after the last day
  loaded (days POWER hasn't published yet are dropped and asked again the next day).

Docs: https://power.larc.nasa.gov/docs/services/api/temporal/daily/
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

POWER_REGIONAL_URL = "https://power.larc.nasa.gov/api/temporal/daily/regional"
COMMUNITY = "AG"
FILL_VALUE = -999.0
MAX_BOX_DEG = 10  # API limit per side
REQUEST_TIMEOUT = 300
RETRIES = 5
RETRY_WAIT_S = 30  # POWER answers 429 (or 5xx) when hit too hard: back off, 30 s, 60 s, ...
MIN_INTERVAL_S = 1.0  # between two requests (one at a time): polite, no published rate limit
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = _last_request + MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

# POWER parameter -> bronze column. What the map shows: the day's max temperature,
# total rain and mean wind (speed and the direction it comes from).
PARAMETERS = {
    "T2M_MAX": "t2m_max",  # °C
    "PRECTOTCORR": "precipitation",  # mm/day (MERRA-2, bias-corrected)
    "WS10M": "ws10m",  # m/s, daily mean at 10 m
    "WD10M": "wd10m",  # degrees, where the wind comes from
}


class Area:
    """Bounding box in degrees, from FIRMS' format: "world" or "west,south,east,north"."""

    def __init__(self, west=None, south=None, east=None, north=None):
        self.west, self.south, self.east, self.north = west, south, east, north

    @classmethod
    def parse(cls, area: str) -> Area:
        if area.strip().lower() == "world":
            return cls()
        west, south, east, north = (float(v) for v in area.split(","))
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError(f"Invalid area {area!r}: expected west,south,east,north with west < east, south < north")
        return cls(west, south, east, north)

    def __eq__(self, other):
        return isinstance(other, Area) and self.box() == other.box()

    def box(self) -> tuple[float, float, float, float]:
        return (-180, -90, 180, 90) if self.west is None else (self.west, self.south, self.east, self.north)

    def clip(self, boxes):
        """Boxes cut to the area (empty ones dropped)."""
        aw, as_, ae, an = self.box()
        out = [(max(w, aw), max(s, as_), min(e, ae), min(n, an)) for w, s, e, n in boxes]
        return [b for b in out if b[0] < b[2] and b[1] < b[3]]


def fetch_regional(parameter: str, box: tuple[float, float, float, float], start: date, end: date,
                   session: requests.Session | None = None) -> dict[tuple[float, float, date], float]:
    """{(lat, lon, day): value} of one parameter over one box (west, south, east, north)
    and a date range. Missing values (POWER's fill value) are left out."""
    west, south, east, north = box
    params = {
        "parameters": parameter, "community": COMMUNITY, "format": "JSON", "time-standard": "UTC",
        "latitude-min": south, "latitude-max": north, "longitude-min": west, "longitude-max": east,
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
    }
    get = (session or requests).get
    for attempt in range(RETRIES + 1):
        _throttle()
        response = get(POWER_REGIONAL_URL, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
            logger.warning("POWER %s for %s %s: waiting %ss (%s/%s)", response.status_code, parameter, box,
                           RETRY_WAIT_S * (attempt + 1), attempt + 1, RETRIES)
            time.sleep(RETRY_WAIT_S * (attempt + 1))
            continue
        if response.status_code != 200:
            raise RuntimeError(f"POWER HTTP {response.status_code} for {parameter} {box}: {response.text[:300]}")
        break
    out = {}
    for feature in response.json().get("features", []):
        lon, lat = feature["geometry"]["coordinates"][:2]
        for day, value in feature["properties"]["parameter"][parameter].items():
            if value is not None and value != FILL_VALUE:
                out[(lat, lon, datetime.strptime(day, "%Y%m%d").date())] = value
    return out


def split_box(box: tuple[float, float, float, float], size: float = MAX_BOX_DEG) -> list[tuple[float, float, float, float]]:
    """A box cut into pieces of at most `size` degrees per side (the API's limit)."""
    west, south, east, north = box
    out = []
    lat = south
    while lat < north:
        lon = west
        while lon < east:
            out.append((lon, lat, min(east, lon + size), min(north, lat + size)))
            lon += size
        lat += size
    return out


def fetch_box(box: tuple[float, float, float, float], start: date, end: date, area: str,
              session: requests.Session | None = None):
    """Every PARAMETER over one box (<= 10°) and date range, as an Arrow table with one
    row per grid cell and day. Cells/days with no value for any parameter are dropped
    (e.g. the last days, not published yet)."""
    import pyarrow as pa

    values = {p: fetch_regional(p, box, start, end, session) for p in PARAMETERS}
    keys = sorted(set().union(*values.values()), key=lambda k: (k[2], k[0], k[1]))
    now = datetime.now().astimezone()
    columns = {
        "obs_date": [k[2] for k in keys],
        "lat": [k[0] for k in keys],
        "lon": [k[1] for k in keys],
        **{col: [values[p].get(k) for k in keys] for p, col in PARAMETERS.items()},
    }
    table = pa.table(columns)
    n = table.num_rows
    return table.append_column("_box", pa.array([",".join(f"{v:g}" for v in box)] * n)) \
        .append_column("_area", pa.array([area] * n)) \
        .append_column("_source", pa.array(["NASA POWER daily regional"] * n)) \
        .append_column("_extracted_at", pa.array([now] * n, type=pa.timestamp("us", tz="UTC")))


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
