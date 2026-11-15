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


def _build_lake(root: Path) -> None:
    root.mkdir(parents=True)
    sqlite = sqlite3.connect(root / "catalog.sqlite")
    sqlite.execute("PRAGMA journal_mode=WAL")  # as the ETL does (include/firms/config.py)
    sqlite.close()
    con = _attach(root)
    con.execute("CREATE SCHEMA lake.silver; CREATE SCHEMA lake.gold;")
    # MODIS: silver table + gold view, like dbt builds them (with the stored h3_r6 cell).
    # Two fires in Argentina, one in the US, one invalid row.
    con.execute(
        """
        CREATE TABLE lake.silver.modis_nrt AS
        select *, case when latitude between -90 and 90 then h3_latlng_to_cell(latitude, longitude, 6) end as h3_r6
        from (values
            (-34.6, -58.4, date '2026-11-10', timestamp '2026-11-10 12:30', 10.0, 'D', 'Aqua',  85, 320.0, 290.0, '1', null),
            (-31.4, -64.2, date '2026-11-11', timestamp '2026-11-11 03:00',  5.0, 'N', 'Terra', 50, 310.0, 285.0, '1', null),
            ( 40.0, -100.0, date '2026-11-11', timestamp '2026-11-11 18:00', 20.0, 'D', 'Aqua', 90, 330.0, 295.0, '2', null),
            ( 95.0, 10.0, date '2026-11-11', timestamp '2026-11-11 18:00', 20.0, 'D', 'Aqua', 90, 330.0, 295.0, '2', 'invalid_coordinates')
        ) t(latitude, longitude, acq_date, acq_datetime_utc, frp, daynight, satellite,
            confidence, brightness, bright_t31, _dlt_load_id, dq_issue)
        """
    )
    con.execute("CREATE VIEW lake.gold.modis_nrt AS select * exclude (dq_issue) from lake.silver.modis_nrt where dq_issue is null")
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
    assert set(body) == {"modis_nrt", "modis_sp", "viirs_nrt", "viirs_sp", "harmonized_nrt", "harmonized_sp"}
    assert body["modis_nrt"] == {
        "id": "modis_nrt", "sensor": "modis", "product": "nrt", "available": True,
        "rows": 3, "start_date": "2026-11-10", "end_date": "2026-11-11",
    }
    assert body["viirs_sp"]["available"] is False and body["viirs_sp"]["rows"] == 0
    assert body["harmonized_nrt"]["available"] is True and body["harmonized_nrt"]["sensor"] == "harmonized"
    assert body["harmonized_sp"]["available"] is False


def test_detections_geojson(client):
    body = client.get("/datasets/modis_nrt/detections").json()
    assert body["type"] == "FeatureCollection"
    assert body["truncated"] is False
    assert len(body["features"]) == 3  # the all-null row is excluded
    first = body["features"][0]  # most recent first
    assert first["type"] == "Feature"
    assert first["geometry"] == {"type": "Point", "coordinates": [-100.0, 40.0]}
    assert first["properties"] == {
        "sensor": "modis", "satellite": "Aqua", "acq_datetime_utc": "2026-11-11T18:00:00",
        "frp": 20.0, "confidence": "90", "daynight": "D", "brightness": 330.0,
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
    assert len(client.get(f"/datasets/modis_nrt/detections?{query}").json()["features"]) == expected


def test_detections_limit_sets_truncated(client):
    body = client.get("/datasets/modis_nrt/detections?limit=2").json()
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
    assert client.get(f"/datasets/modis_nrt/detections?{query}").status_code == 422


def test_unknown_dataset_is_422(client):
    assert client.get("/datasets/goes_nrt/detections").status_code == 422


def test_daily_stats(client):
    body = client.get("/datasets/modis_nrt/stats/daily").json()
    assert body == [
        {"date": "2026-11-10", "detections": 1, "day": 1, "night": 0, "frp_sum": 10.0, "frp_mean": 10.0},
        {"date": "2026-11-11", "detections": 2, "day": 1, "night": 1, "frp_sum": 25.0, "frp_mean": 12.5},
    ]


def test_grid_stats(client):
    body = client.get("/datasets/modis_nrt/stats/grid?cell=10").json()
    assert body["type"] == "FeatureCollection" and body["cell"] == 10
    assert sum(f["properties"]["detections"] for f in body["features"]) == 3
    # (-58.4, -34.6) falls in the cell [-60, -50] x [-40, -30]
    rings = [f["geometry"]["coordinates"][0] for f in body["features"]]
    assert any({tuple(p) for p in ring} == {(-60, -40), (-50, -40), (-50, -30), (-60, -30)} for ring in rings)


ARGENTINA = '{"type":"Polygon","coordinates":[[[-74,-56],[-53,-56],[-53,-21],[-74,-21],[-74,-56]]]}'


def test_zone_filters_detections(client):
    r = client.get("/datasets/modis_nrt/detections", params={"zone": ARGENTINA})
    assert len(r.json()["features"]) == 2


def test_zone_multipolygon_and_bbox_combined(client):
    zone = '{"type":"MultiPolygon","coordinates":[[[[-74,-56],[-53,-56],[-53,-21],[-74,-21],[-74,-56]]],[[[-110,30],[-90,30],[-90,50],[-110,50],[-110,30]]]]}'
    assert len(client.get("/datasets/modis_nrt/detections", params={"zone": zone}).json()["features"]) == 3
    # bbox over the US only: intersected with the zone -> 1
    r = client.get("/datasets/modis_nrt/detections", params={"zone": zone, "bbox": "-120,20,-80,60"})
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
    assert client.get("/datasets/modis_nrt/detections", params={"zone": zone}).status_code == 422


def test_hex_stats(client):
    body = client.get("/datasets/modis_nrt/stats/hex?res=3").json()
    assert body["type"] == "FeatureCollection" and body["resolution"] == 3
    assert sum(f["properties"]["detections"] for f in body["features"]) == 3
    assert len({f["properties"]["h3"] for f in body["features"]}) == len(body["features"])
    assert all(f["geometry"]["type"] == "Polygon" for f in body["features"])
    # Zone filter applies to hexagons too.
    body = client.get("/datasets/modis_nrt/stats/hex", params={"res": 3, "zone": ARGENTINA}).json()
    assert sum(f["properties"]["detections"] for f in body["features"]) == 2


def test_monthly_stats(client):
    assert client.get("/datasets/modis_nrt/stats/monthly").json() == [
        {"year": 2026, "month": 11, "detections": 3, "frp_sum": 35.0},
    ]
    assert client.get("/datasets/harmonized_nrt/stats/monthly").json() == [
        {"year": 2026, "month": 11, "detections": 1, "frp_sum": 7.5},
    ]


def test_no_lake_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("LAKE_PATH", str(tmp_path / "missing"))
    with TestClient(app) as c:
        assert all(not d["available"] for d in c.get("/datasets").json())
        assert c.get("/datasets/modis_nrt/detections").status_code == 404


def test_sees_new_commits_without_restart(client, lake):
    assert client.get("/datasets").json()[0]["rows"] == 3
    writer = _attach(lake)  # an ETL run committing while the backend is up
    writer.execute(
        "insert into lake.silver.modis_nrt values "
        "(-30.0, -60.0, date '2026-11-12', timestamp '2026-11-12 01:00', 1.0, 'N', 'Aqua', 70, 300.0, 280.0, '3', null, "
        "h3_latlng_to_cell(-30.0, -60.0, 6))"
    )
    writer.close()
    body = {d["id"]: d for d in client.get("/datasets").json()}
    assert body["modis_nrt"]["rows"] == 4
    assert body["modis_nrt"]["end_date"] == "2026-11-12"


def _cell(lat, lon, res=6):
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    return con.execute(f"select h3_h3_to_string(h3_latlng_to_cell({lat}, {lon}, {res}))").fetchone()[0]


def test_hex_res6_is_the_fixed_cell_of_each_detection(client):
    cells = {f["properties"]["h3"] for f in client.get("/datasets/modis_nrt/stats/hex?res=6").json()["features"]}
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
    body = client.get(f"/datasets/modis_nrt/stats/hex?res={res}").json()
    assert {f["properties"]["h3"] for f in body["features"]} == expected
    assert sum(f["properties"]["detections"] for f in body["features"]) == 3


def test_dataset_without_h3_column_gets_it_computed(client):
    # harmonized_nrt has no h3_r6 column: the backend computes the same cell.
    cells = {f["properties"]["h3"] for f in client.get("/datasets/harmonized_nrt/stats/hex?res=6").json()["features"]}
    assert cells == {_cell(10.5, 20.5)}


def test_res_finer_than_the_base_cell_is_422(client):
    assert client.get("/datasets/modis_nrt/stats/hex?res=7").status_code == 422


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
