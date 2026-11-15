"""API tests against a tiny DuckLake built in a temp folder.

The lake is created in one folder and then moved, like the real one: the ETL
writes it with container paths and the backend reads it from the host.
"""

import shutil
import sqlite3
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.main import app


def _attach(root: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite; INSTALL h3 FROM community; LOAD h3;")
    # Inlining off: write real Parquet files even for a handful of rows, so the
    # relocated lake must find them through OVERRIDE_DATA_PATH.
    options = f"DATA_PATH '{root}/files/', OVERRIDE_DATA_PATH true, DATA_INLINING_ROW_LIMIT 0" + (", READ_ONLY" if read_only else "")
    con.execute(f"ATTACH 'ducklake:sqlite:{root}/catalog.sqlite' AS lake ({options})")
    return con


# The hexagon gold tables as dbt builds them (etl/dags/dbt/firms/models/gold), with
# only the columns the backend reads. MODIS SP: two fires in Argentina and one in
# the US. MODIS NRT: a version of the Nov 10 fire that SP already covers (not
# preferred), a fire after the last SP day (preferred) and a static heat source.
DETECTIONS = """
    select *, h3_latlng_to_cell(latitude, longitude, 6) as h3_r6, date_trunc('month', acq_date)::date as acq_month
    from (values
        ('modis', 'Aqua',  'sp',  -34.6,  -58.4,  date '2026-11-10', timestamp '2026-11-10 12:30', 10.0, 'D', '85', 320.0, false, true,  '1'),
        ('modis', 'Terra', 'sp',  -31.4,  -64.2,  date '2026-11-11', timestamp '2026-11-11 03:00',  5.0, 'N', '50', 310.0, false, true,  '1'),
        ('modis', 'Aqua',  'sp',   40.0, -100.0,  date '2026-11-11', timestamp '2026-11-11 18:00', 20.0, 'D', '90', 330.0, false, true,  '2'),
        ('modis', 'Aqua',  'nrt', -34.61, -58.41, date '2026-11-10', timestamp '2026-11-10 12:31',  9.0, 'D', '80', 318.0, false, false, '3'),
        ('modis', 'Aqua',  'nrt', -30.0,  -60.0,  date '2026-11-12', timestamp '2026-11-12 01:00',  1.0, 'N', '70', 300.0, false, true,  '3'),
        ('modis', 'Aqua',  'nrt',  10.0,  -64.0,  date '2026-11-12', timestamp '2026-11-12 18:00', 50.0, 'D', '95', 400.0, true,  true,  '3')
    ) t(sensor, satellite, product, latitude, longitude, acq_date, acq_datetime_utc, frp, daynight,
        confidence_raw, brightness, is_static_source, is_preferred, _dlt_load_id)
"""

DAILY = """
    select h3_r6, acq_date, acq_month, product, sensor, satellite, daynight, bool_and(is_preferred) as is_preferred,
           count(*) filter (where not is_static_source) as fire_detections,
           sum(frp) filter (where not is_static_source) as fire_frp_sum,
           count(*) filter (where is_static_source) as static_detections,
           sum(frp) filter (where is_static_source) as static_frp_sum,
           max(_dlt_load_id) as last_load_id
    from lake.gold.fct_hexagon_detections
    group by all
"""

HEXAGONS = """
    select h3_r6, h3_cell_to_latlng(h3_r6)[1] as center_lat, h3_cell_to_latlng(h3_r6)[2] as center_lon,
           min(acq_date) as first_date, max(acq_date) as last_date
    from lake.gold.fct_hexagon_daily
    group by h3_r6
"""


def _build_lake(root: Path) -> None:
    root.mkdir(parents=True)
    sqlite = sqlite3.connect(root / "catalog.sqlite")
    sqlite.execute("PRAGMA journal_mode=WAL")  # as the ETL does (include/firms/config.py)
    sqlite.close()
    con = _attach(root)
    con.execute("CREATE SCHEMA lake.gold;")
    con.execute(f"CREATE TABLE lake.gold.fct_hexagon_detections AS {DETECTIONS}")
    con.execute(f"CREATE TABLE lake.gold.fct_hexagon_daily AS {DAILY}")
    con.execute(f"CREATE TABLE lake.gold.dim_hexagon AS {HEXAGONS}")
    # Harmonized gold with only the minimum contract: no satellite/confidence/brightness, no dlt columns.
    con.execute(
        """
        CREATE TABLE lake.gold.harmonized_nrt AS
        select 10.5 as latitude, 20.5 as longitude, date '2026-11-12' as acq_date,
               timestamp '2026-11-12 10:00' as acq_datetime_utc, 7.5 as frp, 'D' as daynight
        """
    )
    con.close()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    written_at = tmp_path / "etl_container_path"
    _build_lake(written_at)
    lake = tmp_path / "host_path" / "lake"
    shutil.move(written_at, lake)
    monkeypatch.setenv("LAKE_PATH", str(lake))
    return lake


@pytest.fixture
def client(lake):
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_datasets_lists_all_three_sensors(client):
    body = {d["id"]: d for d in client.get("/datasets").json()}
    assert set(body) == {
        "modis_best", "modis_sp", "modis_nrt", "viirs_best", "viirs_sp", "viirs_nrt", "harmonized_nrt", "harmonized_sp",
    }
    assert body["modis_sp"] == {
        "id": "modis_sp", "sensor": "modis", "product": "sp", "available": True,
        "rows": 3, "start_date": "2026-11-10", "end_date": "2026-11-11",
    }
    # rows count wildfires only: the NRT static source is not one
    assert body["modis_nrt"]["rows"] == 2
    # best = all SP + the NRT fire after the last SP day (the NRT copy of Nov 10 is not preferred)
    assert body["modis_best"] == {
        "id": "modis_best", "sensor": "modis", "product": "best", "available": True,
        "rows": 4, "start_date": "2026-11-10", "end_date": "2026-11-12",
    }
    assert body["viirs_sp"]["available"] is False and body["viirs_sp"]["rows"] == 0
    assert body["harmonized_nrt"]["available"] is True and body["harmonized_nrt"]["sensor"] == "harmonized"
    assert body["harmonized_sp"]["available"] is False


def test_detections_geojson(client):
    body = client.get("/datasets/modis_sp/detections").json()
    assert body["type"] == "FeatureCollection"
    assert body["truncated"] is False
    assert len(body["features"]) == 3
    first = body["features"][0]  # most recent first
    assert first["type"] == "Feature"
    assert first["geometry"] == {"type": "Point", "coordinates": [-100.0, 40.0]}
    assert first["properties"] == {
        "sensor": "modis", "product": "sp", "is_static_source": False, "satellite": "Aqua",
        "acq_datetime_utc": "2026-11-11T18:00:00", "frp": 20.0, "confidence": "90", "daynight": "D", "brightness": 330.0,
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("bbox=-74,-56,-53,-21", 2),  # Argentina
        ("start=2026-11-11", 2),
        ("end=2026-11-10", 1),
        ("daynight=N", 1),
        ("bbox=-74,-56,-53,-21&daynight=D", 1),
    ],
)
def test_detections_filters(client, query, expected):
    assert len(client.get(f"/datasets/modis_sp/detections?{query}").json()["features"]) == expected


def test_detections_limit_sets_truncated(client):
    body = client.get("/datasets/modis_sp/detections?limit=2").json()
    assert len(body["features"]) == 2
    assert body["truncated"] is True


def test_harmonized_with_minimum_columns(client):
    body = client.get("/datasets/harmonized_nrt/detections").json()
    props = body["features"][0]["properties"]
    assert props["sensor"] == "harmonized"
    assert props["satellite"] is None and props["confidence"] is None and props["brightness"] is None
    assert props["frp"] == 7.5


def test_dataset_without_files_is_404(client):
    r = client.get("/datasets/viirs_nrt/detections")
    assert r.status_code == 404
    assert "viirs_nrt" in r.json()["detail"]


@pytest.mark.parametrize(
    "query",
    ["bbox=a,b,c,d", "bbox=10,0,5,1", "bbox=0,0,200,1", "start=2026-11-11&end=2026-11-10", "daynight=X"],
)
def test_invalid_params_are_422(client, query):
    assert client.get(f"/datasets/modis_sp/detections?{query}").status_code == 422


def test_unknown_dataset_is_422(client):
    assert client.get("/datasets/goes_nrt/detections").status_code == 422


def test_daily_stats(client):
    body = client.get("/datasets/modis_sp/stats/daily").json()
    assert body == [
        {"date": "2026-11-10", "detections": 1, "day": 1, "night": 0, "frp_sum": 10.0, "frp_mean": 10.0, "static_detections": 0},
        {"date": "2026-11-11", "detections": 2, "day": 1, "night": 1, "frp_sum": 25.0, "frp_mean": 12.5, "static_detections": 0},
    ]


def test_grid_stats(client):
    body = client.get("/datasets/modis_sp/stats/grid?cell=10").json()
    assert body["type"] == "FeatureCollection" and body["cell"] == 10
    assert sum(f["properties"]["detections"] for f in body["features"]) == 3
    # (-58.4, -34.6) falls in the cell [-60, -50] x [-40, -30]
    rings = [f["geometry"]["coordinates"][0] for f in body["features"]]
    assert any({tuple(p) for p in ring} == {(-60, -40), (-50, -40), (-50, -30), (-60, -30)} for ring in rings)


ARGENTINA = '{"type":"Polygon","coordinates":[[[-74,-56],[-53,-56],[-53,-21],[-74,-21],[-74,-56]]]}'


def test_zone_filters_detections(client):
    r = client.get("/datasets/modis_sp/detections", params={"zone": ARGENTINA})
    assert len(r.json()["features"]) == 2


def test_zone_multipolygon_and_bbox_combined(client):
    zone = '{"type":"MultiPolygon","coordinates":[[[[-74,-56],[-53,-56],[-53,-21],[-74,-21],[-74,-56]]],[[[-110,30],[-90,30],[-90,50],[-110,50],[-110,30]]]]}'
    assert len(client.get("/datasets/modis_sp/detections", params={"zone": zone}).json()["features"]) == 3
    # bbox over the US only: intersected with the zone -> 1
    r = client.get("/datasets/modis_sp/detections", params={"zone": zone, "bbox": "-120,20,-80,60"})
    assert len(r.json()["features"]) == 1


@pytest.mark.parametrize(
    "zone",
    [
        "not json",
        '{"type":"Point","coordinates":[0,0]}',
        '{"type":"Polygon","coordinates":[[[0,0],[1,1]]]}',
        '{"type":"Polygon","coordinates":[[[0,0],[200,0],[0,1],[0,0]]]}',
    ],
)
def test_invalid_zone_is_422(client, zone):
    assert client.get("/datasets/modis_sp/detections", params={"zone": zone}).status_code == 422


def test_hex_stats(client):
    body = client.get("/datasets/modis_sp/stats/hex?res=3").json()
    assert body["type"] == "FeatureCollection" and body["resolution"] == 3
    assert sum(f["properties"]["detections"] for f in body["features"]) == 3
    assert len({f["properties"]["h3"] for f in body["features"]}) == len(body["features"])
    assert all(f["geometry"]["type"] == "Polygon" for f in body["features"])
    # Zone filter applies to hexagons too.
    body = client.get("/datasets/modis_sp/stats/hex", params={"res": 3, "zone": ARGENTINA}).json()
    assert sum(f["properties"]["detections"] for f in body["features"]) == 2


def test_monthly_stats(client):
    assert client.get("/datasets/modis_sp/stats/monthly").json() == [
        {"year": 2026, "month": 11, "detections": 3, "frp_sum": 35.0, "static_detections": 0},
    ]
    assert client.get("/datasets/harmonized_nrt/stats/monthly").json() == [
        {"year": 2026, "month": 11, "detections": 1, "frp_sum": 7.5, "static_detections": 0},
    ]


def test_no_lake_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("LAKE_PATH", str(tmp_path / "missing"))
    with TestClient(app) as c:
        assert all(not d["available"] for d in c.get("/datasets").json())
        assert c.get("/datasets/modis_sp/detections").status_code == 404


def test_sees_new_commits_without_restart(client, lake):
    body = {d["id"]: d for d in client.get("/datasets").json()}
    assert body["modis_sp"]["rows"] == 3
    writer = _attach(lake)  # an ETL run committing while the backend is up
    writer.execute(
        "insert into lake.gold.fct_hexagon_daily (h3_r6, acq_date, acq_month, product, sensor, satellite, daynight, "
        "is_preferred, fire_detections, fire_frp_sum, static_detections, static_frp_sum, last_load_id) values "
        "(h3_latlng_to_cell(-30.0, -60.0, 6), date '2026-11-13', date '2026-11-01', 'sp', 'modis', 'Aqua', 'N', "
        "true, 2, 3.0, 0, null, '4')"
    )
    writer.close()
    body = {d["id"]: d for d in client.get("/datasets").json()}
    assert body["modis_sp"]["rows"] == 5
    assert body["modis_sp"]["end_date"] == "2026-11-13"


def test_static_sources_are_flagged_and_counted_apart(client):
    points = client.get("/datasets/modis_nrt/detections").json()["features"]
    assert sorted(f["properties"]["is_static_source"] for f in points) == [False, False, True]
    assert client.get("/datasets/modis_nrt/stats/monthly").json() == [
        {"year": 2026, "month": 11, "detections": 2, "frp_sum": 10.0, "static_detections": 1},
    ]
    static_hex = [f for f in client.get("/datasets/modis_nrt/stats/hex?res=6").json()["features"]
                  if f["properties"]["static_detections"]]
    assert [(f["properties"]["h3"], f["properties"]["detections"]) for f in static_hex] == [(_cell(10.0, -64.0), 0)]


def test_best_is_one_continuous_series(client):
    # SP for Nov 10-11, then only the NRT days after it; the NRT copy of Nov 10 is left out.
    days = client.get("/datasets/modis_best/stats/daily").json()
    assert [(d["date"], d["detections"], d["static_detections"]) for d in days] == [
        ("2026-11-10", 1, 0), ("2026-11-11", 2, 0), ("2026-11-12", 1, 1),
    ]
    products = sorted(f["properties"]["product"] for f in client.get("/datasets/modis_best/detections").json()["features"])
    assert products == ["nrt", "nrt", "sp", "sp", "sp"]


def test_hex_zone_selects_hexagons_by_center(client):
    # The Argentina zone holds the centers of the two Argentine fires' hexagons, not the US one.
    body = client.get("/datasets/modis_sp/stats/hex", params={"res": 6, "zone": ARGENTINA}).json()
    assert {f["properties"]["h3"] for f in body["features"]} == {_cell(-34.6, -58.4), _cell(-31.4, -64.2)}


def _cell(lat, lon, res=6):
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    return con.execute(f"select h3_h3_to_string(h3_latlng_to_cell({lat}, {lon}, {res}))").fetchone()[0]


def test_hex_res6_is_the_fixed_cell_of_each_detection(client):
    cells = {f["properties"]["h3"] for f in client.get("/datasets/modis_sp/stats/hex?res=6").json()["features"]}
    assert cells == {_cell(-34.6, -58.4), _cell(-31.4, -64.2), _cell(40.0, -100.0)}


@pytest.mark.parametrize("res", [0, 1, 3, 5])
def test_coarser_res_rolls_up_parents(client, res):
    # Each coarse cell is the parent of the detections' res-6 cells, and no detection is lost.
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    expected = {
        con.execute(f"select h3_cell_to_parent('{_cell(lat, lon)}', {res})").fetchone()[0]
        for lat, lon in [(-34.6, -58.4), (-31.4, -64.2), (40.0, -100.0)]
    }
    body = client.get(f"/datasets/modis_sp/stats/hex?res={res}").json()
    assert {f["properties"]["h3"] for f in body["features"]} == expected
    assert sum(f["properties"]["detections"] for f in body["features"]) == 3


def test_dataset_without_h3_column_gets_it_computed(client):
    # harmonized_nrt has no h3_r6 column: the backend computes the same cell.
    cells = {f["properties"]["h3"] for f in client.get("/datasets/harmonized_nrt/stats/hex?res=6").json()["features"]}
    assert cells == {_cell(10.5, 20.5)}


def test_res_finer_than_the_base_cell_is_422(client):
    assert client.get("/datasets/modis_sp/stats/hex?res=7").status_code == 422


def test_empty_catalog_file_is_no_lake_yet(tmp_path, monkeypatch):
    # The ETL creates catalog.sqlite (WAL mode) before DuckLake initializes it.
    lake = tmp_path / "lake"
    lake.mkdir()
    sqlite = sqlite3.connect(lake / "catalog.sqlite")
    sqlite.execute("PRAGMA journal_mode=WAL")
    sqlite.close()
    monkeypatch.setenv("LAKE_PATH", str(lake))
    with TestClient(app) as c:
        assert all(not d["available"] for d in c.get("/datasets").json())
