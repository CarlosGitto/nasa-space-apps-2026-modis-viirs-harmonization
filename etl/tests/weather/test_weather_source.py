"""Unit tests for the weather ingest: NASA POWER client, planning and fire zones.

No network: POWER answers are built in memory with the API's real JSON shape.
"""

from datetime import date, timedelta

import pyarrow as pa
import pytest

from include.weather import power
from include.weather.power import Area, fetch_regional, split_box
from include.weather.source import chunk_range, plan_range, unique_cells


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def regional(parameter, cells):
    """POWER daily regional JSON: cells = {(lon, lat): {YYYYMMDD: value}}."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat, 100.0]},
         "properties": {"parameter": {parameter: values}}}
        for (lon, lat), values in cells.items()
    ]}


class FakeSession:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    waits = []
    monkeypatch.setattr(power.time, "sleep", waits.append)
    return waits


def test_fetch_regional_parses_cells_and_drops_fill_values():
    payload = regional("T2M_MAX", {(-71.875, -41.5): {"20250130": 27.1, "20250131": -999.0}})
    session = FakeSession([FakeResponse(200, payload)])
    out = fetch_regional("T2M_MAX", (-80, -50, -70, -40), date(2025, 1, 30), date(2025, 1, 31), session)
    assert out == {(-41.5, -71.875, date(2025, 1, 30)): 27.1}  # the day not published yet is left out
    p = session.calls[0]
    assert (p["parameters"], p["time-standard"], p["start"], p["end"]) == ("T2M_MAX", "UTC", "20250130", "20250131")
    assert (p["longitude-min"], p["latitude-min"], p["longitude-max"], p["latitude-max"]) == (-80, -50, -70, -40)


def test_fetch_regional_backs_off_on_429_then_succeeds(no_waiting):
    ok = FakeResponse(200, regional("WS10M", {(0.0, 0.0): {"20250101": 2.0}}))
    session = FakeSession([FakeResponse(429, text="Too Many Requests"), FakeResponse(503), ok])
    assert fetch_regional("WS10M", (0, 0, 10, 10), date(2025, 1, 1), date(2025, 1, 1), session)
    assert [w for w in no_waiting if w >= power.RETRY_WAIT_S] == [power.RETRY_WAIT_S, 2 * power.RETRY_WAIT_S]


def test_fetch_regional_fails_on_other_errors():
    session = FakeSession([FakeResponse(422, text="maximum of 10 degree range")])
    with pytest.raises(RuntimeError, match="422"):
        fetch_regional("T2M_MAX", (0, 0, 20, 20), date(2025, 1, 1), date(2025, 1, 1), session)


def test_requests_are_spaced_out(monkeypatch, no_waiting):
    clock = iter([100.0, 100.0, 100.2, 100.2])  # second request comes 0.2 s after the first
    monkeypatch.setattr(power.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(power, "_last_request", 0.0)
    power._throttle()
    power._throttle()
    assert no_waiting == [pytest.approx(power.MIN_INTERVAL_S - 0.2)]


def test_split_box_respects_the_10_degree_limit():
    assert split_box((-80, -50, -60, -38)) == [
        (-80, -50, -70, -40), (-70, -50, -60, -40), (-80, -40, -70, -38), (-70, -40, -60, -38)]
    assert split_box((0, 0, 5, 5)) == [(0, 0, 5, 5)]


def test_area_parse_and_clip():
    assert Area.parse("world").box() == (-180, -90, 180, 90)
    sa = Area.parse("-82,-56,-34,13")
    assert sa.clip([(-90, -60, -70, -50), (0, 0, 10, 10)]) == [(-82, -56, -70, -50)]
    with pytest.raises(ValueError):
        Area.parse("-34,-56,-82,13")  # west > east


def test_unique_cells_keeps_one_row_per_cell_and_day():
    t = pa.table({"obs_date": [date(2025, 1, 1)] * 3, "lat": [-40.0, -40.0, -39.5], "lon": [-70.0, -70.0, -70.0],
                  "t2m_max": [20.0, 20.0, 21.0]})
    assert unique_cells(t).num_rows == 2


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


def test_near_fire_filter_keeps_only_cells_in_the_fire_zone():
    import duckdb

    from include.weather.fires import FIRE_ZONE_RES, near_fire_filter

    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    fire = con.execute(f"select h3_latlng_to_cell(-41.5, -71.8, {FIRE_ZONE_RES})").fetchone()[0]
    grid = pa.table({"lat": [-41.5, -41.4, 10.0], "lon": [-71.8, -71.9, 20.0], "t2m_max": [30.0, 31.0, 20.0]})
    kept = near_fire_filter([fire])(grid)
    assert sorted(kept.column("t2m_max").to_pylist()) == [30.0, 31.0]  # the cell far away is dropped
    assert near_fire_filter([])(grid).num_rows == 0


def test_covered_until_stops_at_the_first_long_gap():
    from include.weather.fires import covered_until

    d0 = date(2025, 1, 1)
    days = {d0 + timedelta(days=i) for i in range(10)} | {d0 + timedelta(days=i) for i in range(14, 20)}
    days |= {date(2026, 11, 1)}  # NRT far ahead while SP is still loading: not covered
    assert covered_until(days, d0) == d0 + timedelta(days=19)  # the 4-day gap is tolerated
    assert covered_until(days, d0, max_gap=3) == d0 + timedelta(days=9)
    assert covered_until(days, date(2025, 6, 1)) is None
    assert covered_until(set(), d0) is None


def test_zone_boxes_cover_the_fires_and_merge_contiguous_tiles():
    from include.weather.fires import zone_boxes

    boxes = zone_boxes([(-41.5, -71.8), (-41.5, -61.0), (45.0, 5.0)])
    # -71.8 and -61.0 fall in contiguous tiles, and -61.0 is within the 1° margin of -60:
    # three tiles of the same row, merged into one box (split to <= 10° before POWER).
    assert (-80, -50, -50, -40) in boxes
    assert (0, 40, 10, 50) in boxes
    assert zone_boxes([]) == []
