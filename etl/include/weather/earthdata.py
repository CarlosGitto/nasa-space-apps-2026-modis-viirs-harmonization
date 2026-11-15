"""NASA Earthdata access: Earthdata Login, CMR granule search and OPeNDAP subsets.

Every gridded product (MERRA-2, IMERG) is read the same way:

1. **CMR** (public, no login) lists the granules (one file per day) of a
   collection in a date range, with each granule's OPeNDAP URL.
2. **OPeNDAP** (Earthdata Login) returns only the variables and the
   latitude/longitude window we ask for, as a small NetCDF-4 file. A global
   MERRA-2 hourly file is ~390 MB; South America with 4 variables is ~4 MB.

The index window is computed from the grid's own coordinates (fetched once per
collection), and the order of each variable's dimensions from the granule's DMR
(the DAP4 metadata), so nothing about a grid is hard-coded here.

Docs: https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html
      https://opendap.github.io/documentation/QuickStart.html (DAP4 constraints)
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

CMR_GRANULES_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
EARTHDATA_LOGIN_HOST = "urs.earthdata.nasa.gov"
OPENDAP_HOST = "opendap.earthdata.nasa.gov"
DAP4_NS = {"dap": "http://xml.opendap.org/ns/DAP/4.0#"}
REQUEST_TIMEOUT = 300  # seconds; OPeNDAP builds the subset on the fly


# ---------------------------------------------------------------- session ---


class EarthdataSession(requests.Session):
    """requests session that keeps credentials only for Earthdata Login.

    OPeNDAP redirects to urs.earthdata.nasa.gov to authenticate and back. The
    credentials must survive that redirect but never be sent to any other host
    (requests drops them on every redirect by default). This is NASA's
    recommended pattern for scripted access.
    """

    def rebuild_auth(self, prepared_request, response):
        headers = prepared_request.headers
        if "Authorization" in headers:
            original = urlparse(response.request.url).hostname
            redirect = urlparse(prepared_request.url).hostname
            if original != redirect and EARTHDATA_LOGIN_HOST not in (original, redirect):
                del headers["Authorization"]


def earthdata_session(username: str | None, password: str) -> EarthdataSession:
    """Authenticated session: username + password, or only an Earthdata token as `password`.

    Retries transient server errors (OPeNDAP can answer 5xx under load).
    """
    session = EarthdataSession()
    if username:
        session.auth = (username, password)
    else:
        session.headers["Authorization"] = f"Bearer {password}"
    retry = Retry(total=4, backoff_factor=5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


# -------------------------------------------------------------------- CMR ---


@dataclass(frozen=True)
class Granule:
    """One file of a collection: usually one day of data."""

    title: str
    start: datetime
    opendap_url: str

    @property
    def day(self) -> date:
        return self.start.date()


def latest_granule_day(short_name: str, version: str) -> date | None:
    """Day of the newest published granule of a collection (None if it has none)."""
    params = {"short_name": short_name, "version": version, "sort_key": "-start_date", "page_size": 1}
    response = requests.get(CMR_GRANULES_URL, params=params, timeout=60)
    response.raise_for_status()
    entries = response.json()["feed"]["entry"]
    return datetime.fromisoformat(entries[0]["time_start"].replace("Z", "+00:00")).date() if entries else None


def search_granules(short_name: str, version: str, start: date, end: date) -> list[Granule]:
    """Granules of a collection whose time range starts in [start, end], oldest first.

    CMR only lists what has been published, so this is also how far a product
    reaches: no granule, no data yet. Public endpoint, no login needed.
    """
    params = {
        "short_name": short_name,
        "version": version,
        "temporal": f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z",
        "sort_key": "start_date",
        "page_size": 2000,
    }
    granules: list[Granule] = []
    search_after = None
    while True:
        headers = {"CMR-Search-After": search_after} if search_after else {}
        response = requests.get(CMR_GRANULES_URL, params=params, headers=headers, timeout=60)
        response.raise_for_status()
        entries = response.json()["feed"]["entry"]
        granules += [g for g in (parse_granule(e) for e in entries) if g is not None and start <= g.day <= end]
        search_after = response.headers.get("CMR-Search-After")
        if not entries or not search_after:
            break
    return granules


def parse_granule(entry: dict) -> Granule | None:
    """Granule from a CMR JSON entry, or None if it has no OPeNDAP link."""
    for link in entry.get("links", []):
        href = link.get("href", "")
        if urlparse(href).hostname == OPENDAP_HOST:
            start = datetime.fromisoformat(entry["time_start"].replace("Z", "+00:00"))
            return Granule(title=entry["title"], start=start, opendap_url=href)
    logger.warning("Granule %s has no OPeNDAP link, skipped", entry.get("title"))
    return None


# ---------------------------------------------------------------- OPeNDAP ---


@dataclass(frozen=True)
class Area:
    """Bounding box in degrees. `None` bounds = the whole globe."""

    west: float | None = None
    south: float | None = None
    east: float | None = None
    north: float | None = None

    @classmethod
    def parse(cls, area: str) -> Area:
        """'world' or 'west,south,east,north' (the same format FIRMS uses)."""
        if area.strip().lower() == "world":
            return cls()
        west, south, east, north = (float(v) for v in area.split(","))
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError(f"Invalid area {area!r}: expected west,south,east,north with west < east, south < north")
        return cls(west, south, east, north)


def index_window(coords: list[float], low: float | None, high: float | None) -> tuple[int, int]:
    """First and last index of the grid cells whose centers fall in [low, high]
    widened by half a cell, so every point of the box has its nearest cell.

    coords must be increasing and evenly spaced (MERRA-2 and IMERG grids are).
    """
    if low is None or high is None:
        return 0, len(coords) - 1
    half = (coords[1] - coords[0]) / 2
    inside = [i for i, c in enumerate(coords) if low - half <= c <= high + half]
    if not inside:
        raise ValueError(f"No grid cell between {low} and {high}")
    return inside[0], inside[-1]


@dataclass(frozen=True)
class Layout:
    """What a granule's DMR (DAP4 metadata) says about its variables."""

    dims: dict[str, list[str]]  # variable -> its dimension names, in order
    sizes: dict[str, int]  # dimension -> length


def parse_dmr(dmr_xml: str) -> Layout:
    """Variables, their dimension order and the dimension sizes of a DAP4 DMR document."""
    root = ET.fromstring(dmr_xml)
    sizes = {d.get("name"): int(d.get("size")) for d in root.findall("dap:Dimension", DAP4_NS)}
    dims = {}
    for var in root:
        name = var.get("name")
        if name and not var.tag.endswith("}Dimension"):
            dims[name] = [d.get("name", "").lstrip("/") for d in var.findall("dap:Dim", DAP4_NS)]
    return Layout(dims=dims, sizes=sizes)


def constraint(variables: list[str], layout: Layout, windows: dict[str, tuple[int, int]]) -> str:
    """DAP4 constraint expression: each variable sliced to the window of its dimensions.

    Dimensions without a window (e.g. time) are taken whole. Coordinate variables
    (time, lat, lon) are requested too so the subset is self-describing.
    """
    def sliced(var: str) -> str:
        if var not in layout.dims:
            raise KeyError(f"Variable {var!r} is not in the granule (has: {', '.join(sorted(layout.dims))})")
        parts = []
        for dim in layout.dims[var]:
            lo, hi = windows.get(dim, (0, layout.sizes[dim] - 1))
            parts.append(f"[{lo}:1:{hi}]")
        return f"/{var}{''.join(parts)}"

    coords = [d for d in ("time", "lat", "lon") if d in layout.dims]
    return ";".join(sliced(v) for v in [*variables, *coords])


class OpendapReader:
    """Subsets the granules of one collection to an area and a list of variables."""

    def __init__(self, session: requests.Session, variables: list[str], area: Area, decode_times: bool = True):
        self.session = session
        self.variables = variables
        self.area = area
        self.decode_times = decode_times  # False: leave the time axis as numbers (see source.Product.daily)
        self._layout: Layout | None = None
        self._windows: dict[str, tuple[int, int]] | None = None

    def _get(self, url: str, **params) -> requests.Response:
        response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code in (401, 403):
            # Which host refused it tells the cause apart: Earthdata Login (bad credentials,
            # or an application not approved yet) or the data server. No credentials in it.
            host = urlparse(response.url).hostname
            body = " ".join(response.text.split())[:300]
            hint = (
                "wrong username/password or token"
                if response.status_code == 401
                else "your Earthdata account hasn't approved the application serving the data yet: open "
                f"{url} once in a browser while logged in to Earthdata and approve it (see README, step 7)"
            )
            raise PermissionError(f"Earthdata refused the request ({response.status_code} from {host}): {hint}. "
                                  f"Response: {body}")
        response.raise_for_status()
        return response

    def _prepare(self, granule: Granule) -> None:
        """Once per collection: dimension order (DMR) and the lat/lon index window."""
        import xarray as xr

        self._layout = parse_dmr(self._get(f"{granule.opendap_url}.dmr").text)
        coords = self._get(f"{granule.opendap_url}.dap.nc4", **{"dap4.ce": "/lat;/lon"}).content
        with xr.open_dataset(io.BytesIO(coords), engine="h5netcdf", decode_times=False) as ds:
            lats, lons = ds["lat"].values.tolist(), ds["lon"].values.tolist()
        self._windows = {
            "lat": index_window(lats, self.area.south, self.area.north),
            "lon": index_window(lons, self.area.west, self.area.east),
        }
        logger.info("Grid window for %s: %s", self.area, self._windows)

    def read(self, granule: Granule):
        """The granule's subset as an in-memory xarray Dataset."""
        import xarray as xr

        if self._layout is None:
            self._prepare(granule)
        ce = constraint(self.variables, self._layout, self._windows)
        content = self._get(f"{granule.opendap_url}.dap.nc4", **{"dap4.ce": ce}).content
        return xr.open_dataset(io.BytesIO(content), engine="h5netcdf", decode_times=self.decode_times).load()
