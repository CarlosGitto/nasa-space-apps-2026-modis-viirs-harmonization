"""Unit tests for the weather ingest: Earthdata helpers and the dlt source.

No network: OPeNDAP responses are built in memory with the same structure as
the real MERRA-2 and IMERG subsets.
"""

from datetime import UTC, date, datetime

import numpy as np
import pyarrow as pa
import pytest
import requests
import xarray as xr

from include.weather.earthdata import (
    Area,
    EarthdataSession,
    Granule,
    constraint,
    index_window,
    parse_dmr,
    parse_granule,
)
from include.weather.source import IMERG_LATE, MERRA2_SLV, chunk_range, plan_range, to_arrow

# Trimmed DMR with the shape GES DISC serves for a MERRA-2 hourly granule.
MERRA2_DMR = """<?xml version="1.0" encoding="ISO-8859-1"?>
<Dataset xmlns="http://xml.opendap.org/ns/DAP/4.0#" dapVersion="4.0" name="MERRA2_400.tavg1_2d_slv_Nx.20261001.nc4">
    <Dimension name="lon" size="576"/>
    <Dimension name="lat" size="361"/>
    <Dimension name="time" size="24"/>
    <Float64 name="lon"><Dim name="/lon"/></Float64>
    <Float64 name="lat"><Dim name="/lat"/></Float64>
    <Int32 name="time"><Dim name="/time"/></Int32>
    <Float32 name="T2M"><Dim name="/time"/><Dim name="/lat"/><Dim name="/lon"/></Float32>
    <Float32 name="U10M"><Dim name="/time"/><Dim name="/lat"/><Dim name="/lon"/></Float32>
</Dataset>"""

# IMERG daily stores precipitation as (time, lon, lat): the order must come from the DMR.
IMERG_DMR = """<?xml version="1.0" encoding="ISO-8859-1"?>
<Dataset xmlns="http://xml.opendap.org/ns/DAP/4.0#" dapVersion="4.0" name="3B-DAY-L.MS.MRG.3IMERG.nc4">
    <Dimension name="time" size="1"/>
    <Dimension name="lon" size="3600"/>
    <Dimension name="lat" size="1800"/>
    <Float32 name="precipitation"><Dim name="/time"/><Dim name="/lon"/><Dim name="/lat"/></Float32>
    <Float32 name="lon"><Dim name="/lon"/></Float32>
    <Float32 name="lat"><Dim name="/lat"/></Float32>
    <Int32 name="time"><Dim name="/time"/></Int32>
</Dataset>"""


def test_parse_dmr_reads_dimension_order_and_sizes():
    layout = parse_dmr(IMERG_DMR)
    assert layout.dims["precipitation"] == ["time", "lon", "lat"]
    assert layout.sizes == {"time": 1, "lon": 3600, "lat": 1800}


def test_constraint_slices_each_variable_in_its_own_dimension_order():
    windows = {"lat": (68, 207), "lon": (157, 234)}
    merra = constraint(["T2M", "U10M"], parse_dmr(MERRA2_DMR), windows)
    assert merra == "/T2M[0:1:23][68:1:207][157:1:234];/U10M[0:1:23][68:1:207][157:1:234];" \
                    "/time[0:1:23];/lat[68:1:207];/lon[157:1:234]"
    imerg = constraint(["precipitation"], parse_dmr(IMERG_DMR), windows)
    assert imerg.startswith("/precipitation[0:1:0][157:1:234][68:1:207];")


def test_constraint_names_the_variables_a_granule_has_when_one_is_missing():
    with pytest.raises(KeyError, match="T2M, U10M"):
        constraint(["T2MDEW"], parse_dmr(MERRA2_DMR), {})


def test_index_window_includes_the_nearest_cell_of_every_point_in_the_box():
    lats = [-90 + 0.5 * j for j in range(361)]  # MERRA-2 grid
    lo, hi = index_window(lats, -56, 13)
    assert lats[lo] == -56.0 and lats[hi] == 13.0
    lo, hi = index_window(lats, -55.9, 12.9)  # a box between grid lines keeps the outer cells
    assert lats[lo] == -56.0 and lats[hi] == 13.0
    assert index_window(lats, None, None) == (0, 360)


def test_index_window_rejects_a_box_outside_the_grid():
    with pytest.raises(ValueError):
        index_window([0.05, 0.15, 0.25], 10, 20)


def test_area_parse():
    assert Area.parse("world") == Area()
    assert Area.parse("-82,-56,-34,13") == Area(-82, -56, -34, 13)
    with pytest.raises(ValueError):
        Area.parse("-34,-56,-82,13")  # west > east


def test_parse_granule_takes_the_opendap_link():
    entry = {
        "title": "GPM_3IMERGDL.07:3B-DAY-L.MS.MRG.3IMERG.20261113-S000000-E235959.V07C.nc4",
        "time_start": "2026-11-13T00:00:00.000Z",
        "links": [
            {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://data.gesdisc.earthdata.nasa.gov/x.nc4"},
            {"rel": "http://esipfed.org/ns/fedsearch/1.1/service#",
             "href": "https://opendap.earthdata.nasa.gov/collections/C1/granules/x.nc4"},
        ],
    }
    granule = parse_granule(entry)
    assert granule.day == date(2026, 11, 13)
    assert granule.opendap_url == "https://opendap.earthdata.nasa.gov/collections/C1/granules/x.nc4"
    assert parse_granule({**entry, "links": entry["links"][:1]}) is None


@pytest.mark.parametrize(
    ("last", "forced", "expected"),
    [
        (None, (None, None), (date(2025, 1, 1), date(2026, 11, 14))),  # empty table: everything from the default start
        (date(2026, 11, 10), (None, None), (date(2026, 11, 11), date(2026, 11, 14))),  # resume, up to `until`
        (date(2026, 11, 14), (None, None), None),  # up to date
        (date(2026, 11, 10), (date(2025, 3, 1), date(2025, 3, 31)), (date(2025, 3, 1), date(2025, 3, 31))),
    ],
)
def test_plan_range(last, forced, expected):
    assert plan_range(last, date(2025, 1, 1), date(2026, 11, 14), *forced) == expected


def test_chunk_range_covers_every_day_once():
    chunks = chunk_range(date(2025, 1, 1), date(2025, 3, 5), 31)
    assert chunks == [(date(2025, 1, 1), date(2025, 1, 31)), (date(2025, 2, 1), date(2025, 3, 3)),
                      (date(2025, 3, 4), date(2025, 3, 5))]
    assert chunk_range(date(2025, 1, 1), date(2025, 1, 1), 31) == [(date(2025, 1, 1), date(2025, 1, 1))]


def _granule(day: date) -> Granule:
    return Granule("g.nc4", datetime(day.year, day.month, day.day, tzinfo=UTC), "https://opendap.earthdata.nasa.gov/g")


def test_to_arrow_merra2_one_row_per_cell_and_hour():
    times = np.datetime64("2026-10-01T00:30") + np.arange(24) * np.timedelta64(1, "h")
    shape = (24, 2, 3)
    ds = xr.Dataset(
        {v: (("time", "lat", "lon"), np.full(shape, 290.0, "f4")) for v in ("T2M", "T2MDEW", "U10M", "V10M")},
        coords={"time": times, "lat": [-35.0, -34.5], "lon": [-60.0, -59.375, -58.75]},
    )
    table = to_arrow(ds, MERRA2_SLV, _granule(date(2026, 10, 1)), "-82,-56,-34,13")
    assert table.num_rows == 24 * 2 * 3
    assert table.column_names[:8] == ["obs_date", "time_utc", "lat", "lon", "t2m", "t2mdew", "u10m", "v10m"]
    assert set(table.column("obs_date").to_pylist()) == {date(2026, 10, 1)}
    time_type = table.schema.field("time_utc").type
    assert pa.types.is_timestamp(time_type) and time_type.tz == "UTC"
    assert table.column("time_utc")[0].as_py() == datetime(2026, 10, 1, 0, 30, tzinfo=UTC)


def test_to_arrow_imerg_handles_lon_before_lat_and_keeps_missing_as_null():
    precip = np.array([[[1.0, np.nan], [0.0, 2.5], [3.0, 4.0]]], "f4")  # (time, lon, lat)
    ds = xr.Dataset(
        {"precipitation": (("time", "lon", "lat"), precip)},
        # Undecoded time axis, as read with decode_times=False (IMERG's "julian" calendar).
        coords={"time": [20405.0], "lon": [-59.95, -59.85, -59.75], "lat": [-34.95, -34.85]},
    )
    table = to_arrow(ds, IMERG_LATE, _granule(date(2026, 11, 13)), "world")
    assert set(table.column("obs_date").to_pylist()) == {date(2026, 11, 13)}  # the day comes from CMR
    assert table.column("time_utc")[0].as_py() == datetime(2026, 11, 13, tzinfo=UTC)
    rows = {(r["lat"], r["lon"]): r["precipitation"] for r in table.to_pylist()}
    assert len(rows) == 6
    assert rows[(-34.85, -59.85)] == 2.5
    assert rows[(-34.85, -59.95)] is None


def _redirect(session: EarthdataSession, from_url: str, to_url: str) -> dict:
    """Headers requests would send on a redirect from from_url to to_url."""
    prepared = requests.Request("GET", to_url, headers={"Authorization": "Bearer t"}).prepare()
    response = requests.Response()
    response.request = requests.Request("GET", from_url).prepare()
    session.rebuild_auth(prepared, response)
    return prepared.headers


def test_session_keeps_credentials_only_for_earthdata_login():
    session = EarthdataSession()
    opendap = "https://opendap.earthdata.nasa.gov/collections/C1/granules/x.nc4"
    assert "Authorization" in _redirect(session, opendap, "https://urs.earthdata.nasa.gov/oauth/authorize")
    assert "Authorization" in _redirect(session, "https://urs.earthdata.nasa.gov/oauth/authorize", opendap)
    assert "Authorization" not in _redirect(session, opendap, "https://some-bucket.s3.amazonaws.com/x.nc4")


def test_near_fire_filter_keeps_only_cells_in_the_fire_zone():
    import duckdb

    from include.weather.fires import FIRE_ZONE_RES, near_fire_filter

    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    fire = con.execute(f"select h3_latlng_to_cell(-41.5, -71.8, {FIRE_ZONE_RES})").fetchone()[0]
    grid = pa.table({"lat": [-41.5, -41.4, 10.0], "lon": [-71.8, -71.9, 20.0], "t2m": [300.0, 301.0, 290.0]})
    kept = near_fire_filter([fire])(grid)
    assert kept.column_names == ["lat", "lon", "t2m"]
    assert sorted(kept.column("t2m").to_pylist()) == [300.0, 301.0]  # the cell far away is dropped
    assert near_fire_filter([])(grid).num_rows == 0


def test_covered_until_stops_at_the_first_long_gap():
    from datetime import timedelta

    from include.weather.fires import covered_until

    d0 = date(2025, 1, 1)
    days = {d0 + timedelta(days=i) for i in range(10)} | {d0 + timedelta(days=i) for i in range(14, 20)}
    days |= {date(2026, 11, 1)}  # NRT far ahead while SP is still loading: not covered
    assert covered_until(days, d0) == d0 + timedelta(days=19)  # the 4-day gap is tolerated
    assert covered_until(days, d0, max_gap=3) == d0 + timedelta(days=9)
    assert covered_until(days, date(2025, 6, 1)) is None  # nothing near that start
    assert covered_until(set(), d0) is None


def test_zone_boxes_cover_the_fires_and_merge_contiguous_tiles():
    from include.weather.fires import zone_boxes

    # Two fires in the same 10° row, contiguous tiles -> one box; one far away -> its own box.
    boxes = zone_boxes([(-41.5, -71.8), (-41.5, -61.0), (45.0, 5.0)])
    # -71.8 and -61.0 fall in contiguous tiles, and -61.0 is within the 1° margin of -60:
    # three tiles of the same row, merged into one box.
    assert (-80, -50, -50, -40) in boxes
    assert (0.0, 40.0, 10.0, 50.0) in boxes
    assert len(boxes) == 2
    # A center within the margin of a tile edge pulls the neighbor tile in too.
    assert zone_boxes([(-40.5, -70.5)]) == [(-80.0, -50.0, -60.0, -40.0), (-80.0, -40.0, -60.0, -30.0)]
    assert zone_boxes([]) == []


class FakeOpendap:
    """Answers like GES DISC's OPeNDAP for a MERRA-2-sized grid (361 x 576, 2 hours):
    the DMR, the coordinates, and subsets cut to the indices of a DAP4 constraint."""

    def __init__(self):
        self.lat = np.arange(-90, 90.01, 0.5)
        self.lon = np.arange(-180, 180, 0.625)
        self.requests = []  # (lat cells, lon cells) of every data request

    def get(self, url, params=None, timeout=None):
        import re

        response = requests.Response()
        response.status_code = 200
        response.url = url
        if url.endswith(".dmr"):
            extra = "".join(f'<Float32 name="{v}"><Dim name="/time"/><Dim name="/lat"/><Dim name="/lon"/></Float32>'
                            for v in ("T2MDEW", "V10M"))
            response._content = MERRA2_DMR.replace('size="24"', 'size="2"').replace("</Dataset>", extra + "</Dataset>").encode()
            return response
        ce = params["dap4.ce"]
        if ce == "/lat;/lon":
            ds = xr.Dataset(coords={"lat": self.lat, "lon": self.lon})
        else:
            (t0, t1), (a0, a1), (o0, o1) = [tuple(map(int, m)) for m in re.findall(r"\[(\d+):1:(\d+)\]", ce.split(";")[0])]
            lat, lon = self.lat[a0:a1 + 1], self.lon[o0:o1 + 1]
            self.requests.append((lat.size, lon.size))
            grid = lat[:, None] * 1000 + lon[None, :]  # value encodes the cell: easy to compare
            ds = xr.Dataset(
                {v: (("time", "lat", "lon"), np.repeat(grid[None], t1 - t0 + 1, 0)) for v in ("T2M", "T2MDEW", "U10M", "V10M")},
                coords={"time": np.datetime64("2025-01-01T00:30") + np.arange(t1 - t0 + 1) * np.timedelta64(1, "h"),
                        "lat": lat, "lon": lon},
            )
        response._content = bytes(ds.to_netcdf(engine="h5netcdf"))
        return response


def test_only_the_boxes_around_fires_are_downloaded_and_nothing_near_them_is_lost():
    import duckdb

    from include.weather.earthdata import OpendapReader
    from include.weather.fires import FIRE_ZONE_RES, near_fire_filter, zone_boxes, zone_centers

    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    fires = [(-41.5, -71.8), (-15.0, -50.0), (10.0, 20.0)]  # Patagonia, central Brazil, Sahel
    zone = [c for (lat, lon) in fires for (c,) in con.execute(
        f"select unnest(h3_grid_disk(h3_latlng_to_cell(?, ?, {FIRE_ZONE_RES}), 1))", [lat, lon]).fetchall()]
    keep = near_fire_filter(zone)
    granule = _granule(date(2025, 1, 1))

    server = FakeOpendap()
    boxes = OpendapReader(server, MERRA2_SLV.file_variables, Area(), boxes=zone_boxes(zone_centers(zone)))
    from include.weather.source import _unique_cells

    near = keep(_unique_cells(pa.concat_tables([to_arrow(ds, MERRA2_SLV, granule, "world") for ds in boxes.read(granule)])))
    assert str(near.schema.field("time_utc").type).endswith("tz=UTC]")
    downloaded = sum(a * b for a, b in server.requests)

    server_all = FakeOpendap()
    everything = OpendapReader(server_all, MERRA2_SLV.file_variables, Area())
    reference = keep(pa.concat_tables([to_arrow(ds, MERRA2_SLV, granule, "world") for ds in everything.read(granule)]))

    # One or two boxes per fire (a fire's ring can cross into the next 10° row), never the globe.
    assert 3 <= len(server.requests) <= 6
    assert downloaded < 0.05 * 361 * 576  # under 5 % of the global grid
    key = lambda t: sorted(zip(t.column("time_utc").to_pylist(), t.column("lat").to_pylist(), t.column("lon").to_pylist(), t.column("t2m").to_pylist()))  # noqa: E731
    assert near.num_rows > 0 and key(near) == key(reference)  # the same cells, the same values
