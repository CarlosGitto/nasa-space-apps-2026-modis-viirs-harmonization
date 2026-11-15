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

from app import db
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
    select *, h3_latlng_to_cell(latitude, longitude, 6) as h3_r6, date_trunc('month', acq_date)::date as acq_month,
           ((extract(hour from acq_datetime_utc) + longitude / 15.0) % 24 + 24) % 24 as solar_hour_local,
           'MODIS' as instrument, '6.1' as version, case when confidence_raw::int >= 80 then 'high' else 'nominal' end as confidence_level,
           brightness - 20 as brightness_aux, 1.0 as scan, 1.0 as track, null::integer as fire_type
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
           max(frp) filter (where not is_static_source) as fire_frp_max,
           count(*) filter (where not is_static_source and confidence_level = 'high') as fire_high_conf,
           count(*) filter (where is_static_source) as static_detections,
           sum(frp) filter (where is_static_source) as static_frp_sum,
           max(_dlt_load_id) as last_load_id
    from lake.gold.fct_hexagon_detections
    group by all
"""

HEXAGONS = """
    select h3_r6, h3_h3_to_string(h3_r6) as h3_id,
           h3_cell_to_latlng(h3_r6)[1] as center_lat, h3_cell_to_latlng(h3_r6)[2] as center_lon,
           h3_cell_area(h3_r6, 'km^2') as area_km2, h3_cell_to_boundary_wkt(h3_r6) as boundary_wkt,
           h3_cell_to_parent(h3_r6, 5) as h3_r5, h3_cell_to_parent(h3_r6, 4) as h3_r4, h3_cell_to_parent(h3_r6, 3) as h3_r3,
           min(acq_date) as first_date, max(acq_date) as last_date, sum(static_detections) > 0 as has_static_source
    from lake.gold.fct_hexagon_daily
    group by h3_r6
"""

MONTHLY = """
    select h3_r6, acq_month, sensor, satellite, string_agg(distinct product, ',' order by product) as products,
           sum(fire_detections) as fire_detections,
           coalesce(sum(fire_detections) filter (where daynight = 'D'), 0) as fire_day,
           coalesce(sum(fire_detections) filter (where daynight = 'N'), 0) as fire_night,
           sum(fire_frp_sum) as fire_frp_sum, max(fire_frp_sum) as fire_frp_max, 0 as fire_high_conf,
           count(distinct acq_date) filter (where fire_detections > 0) as active_days,
           sum(static_detections) as static_detections, max(last_load_id) as last_load_id
    from lake.gold.fct_hexagon_daily where is_preferred
    group by h3_r6, acq_month, sensor, satellite
"""

# A trimmed profile (dbt computes every column, see fct_hexagon_profile.sql).
PROFILE = """
    select h3_r6, sensor,
           min(acq_date) filter (where fire_detections > 0) as first_date,
           max(acq_date) filter (where fire_detections > 0) as last_date,
           count(distinct acq_date) filter (where fire_detections > 0) as active_days,
           sum(fire_detections) as fire_detections, coalesce(sum(fire_frp_sum), 0) as fire_frp_sum,
           max(fire_frp_sum) as fire_frp_max, null::double as fire_frp_mean, null::double as fire_frp_p90,
           null::double as high_conf_share, null::double as day_share, null::date as peak_date,
           null::bigint as peak_day_detections, 11 as peak_month,
           [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, sum(fire_detections), 0] as climatology,
           0 as last_7d, 0 as last_30d, date '2026-11-12' as data_end_date,
           sum(static_detections) as static_detections, null::double as static_share,
           count(distinct satellite) as satellites
    from lake.gold.fct_hexagon_daily where is_preferred
    group by h3_r6, sensor
"""


# Weather gold (etl/dags/dbt/firms/models/gold/fct_weather_daily.sql, fct_precipitation_daily.sql):
# one MERRA-2 cell (0.5° x 0.625°) and two IMERG cells (0.1°) near Buenos Aires.
# MERRA-2 ends Nov 11 (it lags); IMERG goes on to Nov 12, with rain on Oct 20 and Nov 10.
WEATHER = """
    select -34.5 as cell_lat, -58.125 as cell_lon, d::date as obs_date, date_trunc('month', d)::date as obs_month,
           30.0 as t2m_max_c, 15.0 as t2m_min_c, 22.0 as t2m_mean_c, 8.0 as dewpoint_mean_c,
           25.0 as rh_min_pct, 50.0 as rh_mean_pct, 3.1 as vpd_max_kpa, 1.4 as vpd_mean_kpa,
           9.0 as wind_speed_max_ms, 5.0 as wind_speed_mean_ms, 270.0 as wind_dir_mean_deg, 24 as hours,
           '1' as last_load_id
    from range(date '2026-10-01', date '2026-11-12', interval 1 day) t(d)
"""

PRECIPITATION = """
    select cell_lat, -58.45 as cell_lon, d::date as obs_date, date_trunc('month', d)::date as obs_month,
           case when d::date in (date '2026-10-20', date '2026-11-10') then 12.0
                when d::date = date '2026-11-11' then 0.5 else 0.0 end as precipitation_mm,
           case when d::date < date '2026-11-01' then 'final' else 'late' end as imerg_run, '1' as last_load_id
    from range(date '2026-10-01', date '2026-11-13', interval 1 day) t(d), (values (-34.55), (-34.75)) c(cell_lat)
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
    con.execute(f"CREATE TABLE lake.gold.fct_hexagon_monthly AS {MONTHLY}")
    con.execute(f"CREATE TABLE lake.gold.fct_hexagon_profile AS {PROFILE}")
    con.execute(f"CREATE TABLE lake.gold.fct_weather_daily AS {WEATHER}")
    con.execute(f"CREATE TABLE lake.gold.fct_precipitation_daily AS {PRECIPITATION}")
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



# ---------------------------------------------------------------- one hexagon ---

BUENOS_AIRES = (-34.6, -58.4)  # MODIS SP fire (Nov 10) + its NRT copy (not preferred)


def test_hexagon_identity_and_profile(client):
    h = _cell(*BUENOS_AIRES)
    body = client.get(f"/hexagons/{h}").json()
    assert body["h3"] == h and body["resolution"] == 6
    assert body["boundary"]["type"] == "Polygon" and 20 < body["area_km2"] < 50
    assert [p["sensor"] for p in body["profiles"]] == ["modis"]
    assert body["profiles"][0]["fire_detections"] == 1  # the NRT copy is not preferred
    assert len(body["profiles"][0]["climatology"]) == 12


def test_hexagon_monthly(client):
    rows = client.get(f"/hexagons/{_cell(*BUENOS_AIRES)}/monthly").json()
    assert [(r["month"], r["sensor"], r["satellite"], r["products"], r["fire_detections"]) for r in rows] == [
        ("2026-11-01", "modis", "Aqua", "sp", 1),
    ]


def test_hexagon_period_respects_dates(client):
    h = _cell(10.0, -64.0)  # the NRT static source, on Nov 12
    full = client.get(f"/hexagons/{h}/period").json()["sensors"][0]
    assert (full["fire_detections"], full["static_detections"]) == (0, 1)
    assert full["daily"] == [{"date": "2026-11-12", "detections": 0, "static_detections": 1}]
    assert client.get(f"/hexagons/{h}/period?end=2026-11-11").json()["sensors"] == []
    assert client.get(f"/hexagons/{h}/period?start=2026-11-12&end=2026-11-11").status_code == 422


def test_hexagon_detections_respect_dataset(client):
    h = _cell(*BUENOS_AIRES)
    sp = client.get(f"/hexagons/{h}/detections?dataset=modis_sp").json()
    assert [r["product"] for r in sp["rows"]] == ["sp"] and sp["truncated"] is False
    nrt = client.get(f"/hexagons/{h}/detections?dataset=modis_nrt").json()
    assert [(r["product"], r["is_preferred"]) for r in nrt["rows"]] == [("nrt", False)]
    best = client.get(f"/hexagons/{h}/detections?dataset=modis_best").json()
    assert [r["product"] for r in best["rows"]] == ["sp"]  # NRT copy left out of the continuous series
    assert client.get(f"/hexagons/{h}/detections?dataset=harmonized_nrt").status_code == 422


@pytest.mark.parametrize("h3_id", ["not-a-cell", "85c2e313fffffff"])  # garbage / a resolution-5 cell
def test_invalid_hexagon_is_422(client, h3_id):
    assert client.get(f"/hexagons/{h3_id}").status_code == 422


def test_hexagon_without_data_is_404(client):
    assert client.get(f"/hexagons/{_cell(0.0, 0.0)}").status_code == 404



# ------------------------------------------------------ weather of a hexagon ---

def test_hexagon_weather_takes_the_nearest_cell_of_each_grid(client):
    body = client.get(f"/hexagons/{_cell(*BUENOS_AIRES)}/weather").json()
    cells = {c["product"]: c for c in body["cells"]}
    assert cells["merra2"]["center"] == [-58.125, -34.5]
    assert cells["imerg"]["center"] == [-58.45, -34.55]  # not the one at -34.75
    assert cells["imerg"]["distance_km"] < 10
    # What each product covers, so the UI can say which dates to pick.
    assert (cells["merra2"]["first_date"], cells["merra2"]["last_date"]) == ("2026-10-01", "2026-11-11")
    assert (cells["imerg"]["first_date"], cells["imerg"]["last_date"]) == ("2026-10-01", "2026-11-12")
    # Default range: 90 days up to the last day with any weather (IMERG, Nov 12).
    assert (body["start"], body["end"]) == ("2026-08-15", "2026-11-12")
    assert body["days"][0]["date"] == "2026-10-01" and body["days"][-1]["date"] == "2026-11-12"


def test_hexagon_weather_merges_both_products_by_day(client):
    days = {d["date"]: d for d in client.get(f"/hexagons/{_cell(*BUENOS_AIRES)}/weather").json()["days"]}
    nov11, nov12 = days["2026-11-11"], days["2026-11-12"]
    assert (nov11["vpd_max_kpa"], nov11["wind_dir_mean_deg"], nov11["imerg_run"]) == (3.1, 270.0, "late")
    assert nov12["t2m_max_c"] is None and nov12["precipitation_mm"] == 0.0  # MERRA-2 lags, IMERG doesn't


def test_hexagon_weather_accumulations_see_rain_before_the_range(client):
    url = f"/hexagons/{_cell(*BUENOS_AIRES)}/weather?start=2026-11-08&end=2026-11-12"
    days = {d["date"]: d for d in client.get(url).json()["days"]}
    assert list(days) == ["2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
    assert days["2026-11-09"]["days_since_rain"] == 20  # rain on Oct 20, before the range
    assert days["2026-11-10"]["days_since_rain"] == 0
    assert days["2026-11-12"]["days_since_rain"] == 2  # 0.5 mm on Nov 11 is not a rain day
    assert days["2026-11-12"]["precipitation_7d_mm"] == 12.5
    assert days["2026-11-12"]["precipitation_30d_mm"] == 24.5


def test_hexagon_weather_outside_the_loaded_area_is_empty(client):
    body = client.get(f"/hexagons/{_cell(0.0, 0.0)}/weather").json()
    assert (body["cells"], body["days"], body["start"]) == ([], [], None)


def test_hexagon_weather_before_any_weather_load_is_404(client, monkeypatch):
    monkeypatch.setattr(db, "weather_tables", lambda cur: set())
    assert client.get(f"/hexagons/{_cell(*BUENOS_AIRES)}/weather").status_code == 404


@pytest.mark.parametrize("query", ["start=2026-11-12&end=2026-11-11", "start=2020-01-01&end=2026-11-11"])
def test_hexagon_weather_rejects_bad_ranges(client, query):
    assert client.get(f"/hexagons/{_cell(*BUENOS_AIRES)}/weather?{query}").status_code == 422


# ---------------------------------------------------------- a selection ---

def test_selection_summary_includes_only_hexagons_with_history(client):
    ba, nrt_fire, empty = _cell(*BUENOS_AIRES), _cell(-30.0, -60.0), _cell(0.0, 0.0)
    rows = client.post("/hexagons/selection/summary", json={"h3": [ba, nrt_fire, empty, ba]}).json()
    assert {r["h3"]: (r["modis_total"], r["viirs_total"]) for r in rows} == {ba: (1, 0), nrt_fire: (1, 0)}
    # The chosen dates only count in *_period; the busiest hexagon in the period comes first.
    rows = client.post("/hexagons/selection/summary", json={"h3": [ba, nrt_fire], "start": "2026-11-12"}).json()
    assert [(r["h3"], r["modis_period"]) for r in rows] == [(nrt_fire, 1), (ba, 0)]


def test_selection_detections(client):
    ba, nrt_fire = _cell(*BUENOS_AIRES), _cell(-30.0, -60.0)
    body = client.post("/hexagons/selection/detections", json={"h3": [ba, nrt_fire], "dataset": "modis_best"}).json()
    assert [(r["h3"], r["product"]) for r in body["rows"]] == [(nrt_fire, "nrt"), (ba, "sp")]  # most recent first
    one = client.post("/hexagons/selection/detections", json={"h3": [ba, nrt_fire], "dataset": "modis_best", "limit": 1}).json()
    assert one["truncated"] is True and len(one["rows"]) == 1


@pytest.mark.parametrize("body", [
    {"h3": []},
    {"h3": ["not-a-cell"]},
    {"h3": ["85c2e313fffffff"]},  # resolution 5
    {"h3": ["86c2e3117ffffff"], "start": "2026-11-12", "end": "2026-11-11"},
])
def test_selection_rejects_bad_input(client, body):
    assert client.post("/hexagons/selection/summary", json=body).status_code == 422
