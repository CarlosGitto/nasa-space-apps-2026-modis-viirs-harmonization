"""Tests for include/firms/local_files.py: finding and staging FIRMS CSVs downloaded outside Airflow.
Only DuckDB and PyArrow are needed (no Airflow, no lake)."""
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from include.firms.local_files import (LINEAGE_COLUMNS, columns_for, dates_of, discover, prefix_of, product_of,
                                       stage_to_parquet)

MODIS_HEADER = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight,type"
VIIRS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight,type"
VIIRS_HEADER_NO_TYPE = VIIRS_HEADER.rsplit(",", 1)[0]


def write(path: Path, header: str, rows: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(rows) + ("\n" if rows else ""))
    return path


@pytest.mark.parametrize("name,product", [
    ("AR_north_MODIS_SP_2015-01-01.csv", "MODIS_SP"),
    ("Siberia_VIIRS_NOAA20_SP_2019-07-20.csv", "VIIRS_NOAA20_SP"),
    ("viirs_snpp_nrt_today.csv", "VIIRS_SNPP_NRT"),
    ("VIIRS_NOAA21_SP.csv", "VIIRS_NOAA21_SP"),
    ("notes.csv", None),
])
def test_product_of(name, product):
    assert product_of(Path(name)) == product


def test_prefix_and_dates():
    p = Path("year/AR_north_MODIS_SP_2015-01-01.csv")
    assert prefix_of(p, "MODIS_SP") == "AR_north"
    assert dates_of(p) == (date(2015, 1, 1), None)
    q = Path("arg2020/MODIS_SP_2020-09-10_to_2020-09-19.csv")
    assert prefix_of(q, "MODIS_SP") == "arg2020"  # no label in the name: the folder
    assert dates_of(q) == (date(2020, 9, 10), date(2020, 9, 19))
    assert dates_of(Path("front/MODIS_SP.csv")) == (None, None)


def test_columns_for():
    assert columns_for("MODIS_SP")[-1] == "type" and "brightness" in columns_for("MODIS_SP")
    assert "type" not in columns_for("VIIRS_SNPP_NRT") and "bright_ti4" in columns_for("VIIRS_SNPP_NRT")


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "firms"
    write(root / "a" / "ZoneA_MODIS_SP_2020-01-01.csv", MODIS_HEADER, [
        "-33.1,-59.2,320.5,1.1,1.0,2020-01-01,149,Terra,MODIS,64,6.03,290.1,18.5,N,0",
        "-33.2,-59.3,330.0,1.2,1.1,2020-01-02,1752,Aqua,MODIS,80,6.03,300.0,25.0,D,0",
    ])
    # the first detection again (areas overlap) plus a new one
    write(root / "b" / "ZoneB_MODIS_SP_2020-01-01.csv", MODIS_HEADER, [
        "-33.1,-59.2,320.5,1.1,1.0,2020-01-01,149,Terra,MODIS,64,6.03,290.1,18.5,N,0",
        "-10.0,-50.0,310.0,1.0,1.0,2020-01-03,1100,Terra,MODIS,50,6.03,285.0,9.0,D,0",
    ])
    write(root / "ZoneA_VIIRS_SNPP_SP_2020-01-01_to_2020-01-05.csv", VIIRS_HEADER, [
        "-33.15,-59.25,340.0,0.4,0.4,2020-01-02,1750,N,VIIRS,n,2.0NRT,290.0,4.2,D,",
    ])
    write(root / "legacy" / "VIIRS_NOAA20_SP.csv", VIIRS_HEADER_NO_TYPE, [  # SP file whose CSV has no `type`
        "-5.0,-45.0,335.0,0.4,0.4,2020-02-10,0530,N20,VIIRS,h,2.0,288.0,7.7,D",
        "-5.1,-45.1,336.0,0.4,0.4,2020-02-12,0530,N20,VIIRS,l,2.0,288.0,1.7,N",
    ])
    write(root / "empty" / "X_MODIS_SP_2020-01-01.csv", MODIS_HEADER, [])
    write(root / "bad" / "Y_MODIS_SP_2020-01-01.csv", "Exceeding allowed transaction limit.", [])
    write(root / "bad" / "wrong_header_MODIS_SP.csv", "a,b,c", ["1,2,3"])
    write(root / "readme_notes.csv", "x,y", ["1,2"])
    return root


def test_discover_finds_valid_files_and_reports_the_rest(folder):
    files, skipped = discover(folder)
    names = sorted(f.path.name for f in files)
    assert names == ["VIIRS_NOAA20_SP.csv", "ZoneA_MODIS_SP_2020-01-01.csv", "ZoneA_VIIRS_SNPP_SP_2020-01-01_to_2020-01-05.csv",
                     "ZoneB_MODIS_SP_2020-01-01.csv"]
    reasons = {s.path.name: s.reason for s in skipped}
    assert reasons["X_MODIS_SP_2020-01-01.csv"] == "no rows"
    assert "header" in reasons["Y_MODIS_SP_2020-01-01.csv"] and "header" in reasons["wrong_header_MODIS_SP.csv"]
    assert reasons["readme_notes.csv"] == "no FIRMS product in the file name"
    by_name = {f.path.name: f for f in files}
    assert by_name["VIIRS_NOAA20_SP.csv"].prefix == "legacy" and by_name["VIIRS_NOAA20_SP.csv"].has_type is False
    assert by_name["ZoneA_MODIS_SP_2020-01-01.csv"].table == "modis_sp" and by_name["ZoneA_MODIS_SP_2020-01-01.csv"].family == "sp"


def test_discover_filters(folder):
    files, _ = discover(folder, products={"modis_sp"})
    assert {f.product for f in files} == {"MODIS_SP"} and len(files) == 2
    files, _ = discover(folder, prefixes={"ZoneA"})
    assert {f.path.name for f in files} == {"ZoneA_MODIS_SP_2020-01-01.csv", "ZoneA_VIIRS_SNPP_SP_2020-01-01_to_2020-01-05.csv"}
    # name-announced days: the 5-day range file ends 2020-01-05, the others are single days or undated
    files, _ = discover(folder, since=date(2020, 1, 4))
    assert {f.path.name for f in files} == {"VIIRS_NOAA20_SP.csv", "ZoneA_VIIRS_SNPP_SP_2020-01-01_to_2020-01-05.csv"}
    files, _ = discover(folder, since=date(2020, 1, 6))
    assert {f.path.name for f in files} == {"VIIRS_NOAA20_SP.csv"}  # an undated name always stays
    files, _ = discover(folder, until=date(2019, 12, 31))
    assert {f.path.name for f in files} == {"VIIRS_NOAA20_SP.csv"}


def test_stage_to_parquet_shape_dedup_and_lineage(folder, tmp_path):
    files, _ = discover(folder)
    areas = {"ZoneA": "-60,-34,-59,-33"}
    staged = {s.table: s for s in stage_to_parquet(files, tmp_path / "out", areas, folder)}
    assert set(staged) == {"modis_sp", "viirs_snpp_sp", "viirs_noaa20_sp"}

    m = staged["modis_sp"]
    assert (m.rows_read, m.rows_loaded) == (4, 3)  # one detection was in both files
    t = pq.read_table(m.parquet)
    assert t.column_names == columns_for("MODIS_SP") + LINEAGE_COLUMNS
    assert str(t.schema.field("latitude").type) == "double" and str(t.schema.field("acq_date").type) == "date32[day]"
    assert str(t.schema.field("acq_time").type) in ("string", "large_string")
    rows = {r["acq_time"]: r for r in t.to_pylist()}
    assert set(rows) == {"149", "1752", "1100"}  # acq_time kept as text, unpadded (dbt pads it)
    assert rows["149"]["_firms_product"] == "MODIS_SP"
    assert rows["149"]["_firms_area"] == "-60,-34,-59,-33"  # first file wins; ZoneA sorts first
    assert rows["1100"]["_firms_area"] == "local:ZoneB"  # unknown prefix
    assert rows["149"]["_firms_url"] == "local-file://a/ZoneA_MODIS_SP_2020-01-01.csv"  # relative, no absolute path
    assert rows["149"]["_firms_request_start"] == date(2020, 1, 1)
    assert (m.first_day, m.last_day) == (date(2020, 1, 1), date(2020, 1, 3))

    v = pq.read_table(staged["viirs_snpp_sp"].parquet).to_pylist()[0]
    assert v["confidence"] == "n" and v["type"] is None  # empty -> NULL
    assert (v["_firms_request_start"], v["_firms_request_days"]) == (date(2020, 1, 1), 5)  # from the file name range

    legacy = pq.read_table(staged["viirs_noaa20_sp"].parquet)
    assert legacy.column_names == columns_for("VIIRS_NOAA20_SP") + LINEAGE_COLUMNS  # `type` added, empty
    assert {r["type"] for r in legacy.to_pylist()} == {None}
    # no date in the name: request start and days come from the data (2020-02-10 .. 2020-02-12)
    assert {(r["_firms_request_start"], r["_firms_request_days"]) for r in legacy.to_pylist()} == {(date(2020, 2, 10), 3)}


def test_window_days_and_empty_files(tmp_path):
    from datetime import date

    d = tmp_path / "year"
    d.mkdir()
    header = ",".join(columns_for("MODIS_SP")) + "\n"
    (d / "z_MODIS_SP_2020-12-29.csv").write_text(header)  # header only, near the end of the year
    (d / "z_MODIS_SP_2020-01-01.csv").write_text(header + "-1,-1,300,1,1,2020-01-02,1200,Aqua,MODIS,50,6.1,290,10,D,0\n")
    files, skipped = discover(tmp_path, window_days=5, with_empty=True)
    by_name = {f.path.name: f for f in files}
    assert by_name["z_MODIS_SP_2020-12-29.csv"].empty and not by_name["z_MODIS_SP_2020-01-01.csv"].empty
    assert by_name["z_MODIS_SP_2020-12-29.csv"].name_end == date(2020, 12, 31)  # capped at the end of the year
    assert by_name["z_MODIS_SP_2020-01-01.csv"].name_end == date(2020, 1, 5)
    plain, skipped = discover(tmp_path)  # default: empty files are skipped as before
    assert [f.path.name for f in plain] == ["z_MODIS_SP_2020-01-01.csv"] and plain[0].name_end is None
