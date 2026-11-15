"""DuckDB access to the gold layer of the lake.

The lake (data/lake at the repo root) is a DuckLake written by the ETL: a SQLite
catalog (`catalog.sqlite`) plus Parquet files (`files/`). This module attaches
it read-only as `lake`. Every query reads the latest committed snapshot, so new
ETL runs show up immediately, and writes are atomic, so a half-written table is
never seen.

Gold is organized around the hexagon (H3 res 6), the canonical entity:
  gold.dim_hexagon              one row per hexagon (center, area, parents...)
  gold.fct_hexagon_detections   detail: one row per detection, SP and NRT
  gold.fct_hexagon_daily        metrics: hexagon x day x product x satellite x day/night

A MODIS/VIIRS dataset is a filter on those tables (sensor + product, or
is_preferred for the continuous "best" series). Endpoints only see two base
relations, whatever the dataset:
  points_relation  one row per detection             -> /detections, /stats/grid
  cells_relation   one row per hexagon-day-day/night -> /stats/hex, daily, monthly
Spatial filters on cells use the hexagon's center: the hexagon is the unit.

Harmonized datasets (from the harmonization team) keep their own contract: a
table `gold.harmonized_{nrt,sp}` with point columns; their cells are computed
on the fly.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]


def lake_path() -> str:
    """Root of the lake. Read on every call so tests (and deploys) can override it."""
    return os.environ.get("LAKE_PATH", str(REPO_ROOT / "data" / "lake"))


# ---------------------------------------------------------------- datasets ---


class Sensor(StrEnum):
    modis = "modis"
    viirs = "viirs"
    harmonized = "harmonized"


class Product(StrEnum):
    nrt = "nrt"  # near real time
    sp = "sp"  # standard processing (science quality, historical)
    best = "best"  # continuous series: SP where published, NRT after it (is_preferred)


class DatasetId(StrEnum):
    modis_best = "modis_best"
    modis_sp = "modis_sp"
    modis_nrt = "modis_nrt"
    viirs_best = "viirs_best"
    viirs_sp = "viirs_sp"
    viirs_nrt = "viirs_nrt"
    harmonized_nrt = "harmonized_nrt"
    harmonized_sp = "harmonized_sp"


LAKE_ALIAS = "lake"
GOLD_SCHEMA = "gold"
DETECTIONS = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.fct_hexagon_detections"
DAILY = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.fct_hexagon_daily"
HEXAGONS = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.dim_hexagon"
HEXAGON_TABLES = ("fct_hexagon_detections", "fct_hexagon_daily", "dim_hexagon")

# Harmonized datasets (own relation) must have these columns (same names in the lake).
REQUIRED_COLUMNS = ("latitude", "longitude", "acq_date", "acq_datetime_utc", "frp", "daynight")
# The project's fixed spatial unit: the H3 resolution-6 cell of each detection.
# Taken from the lake when the dataset has it (dbt silver computes it once),
# computed from latitude/longitude otherwise (e.g. a harmonized table without it).
H3_RES = 6
H3_COLUMN = "h3_r6"

# Harmonized datasets: API property -> lake column. Missing columns are returned as null.
OPTIONAL_PROPERTIES = ("satellite", "confidence", "brightness")


@dataclass(frozen=True)
class Dataset:
    id: DatasetId
    sensor: Sensor
    product: Product
    columns: dict[str, str] | None = None  # harmonized only: optional property -> lake column

    @property
    def on_hexagon_model(self) -> bool:
        """MODIS/VIIRS read the hexagon gold tables; harmonized has its own relation."""
        return self.sensor is not Sensor.harmonized

    @property
    def relation(self) -> str:
        return f'{LAKE_ALIAS}.{GOLD_SCHEMA}."{self.id}"'

    def predicate(self, alias: str = "") -> str:
        """Filter on the hexagon tables (values come from enums, safe to inline)."""
        p = f"{alias}." if alias else ""
        product = f"{p}is_preferred" if self.product is Product.best else f"{p}product = '{self.product}'"
        return f"{p}sensor = '{self.sensor}' and {product}"


# Provisional: adjust once the harmonization team defines its output columns.
_HARMONIZED = {"satellite": "satellite", "confidence": "confidence", "brightness": "brightness"}

DATASETS: dict[DatasetId, Dataset] = {
    d.id: d
    for d in (
        Dataset(DatasetId.modis_best, Sensor.modis, Product.best),
        Dataset(DatasetId.modis_sp, Sensor.modis, Product.sp),
        Dataset(DatasetId.modis_nrt, Sensor.modis, Product.nrt),
        Dataset(DatasetId.viirs_best, Sensor.viirs, Product.best),
        Dataset(DatasetId.viirs_sp, Sensor.viirs, Product.sp),
        Dataset(DatasetId.viirs_nrt, Sensor.viirs, Product.nrt),
        Dataset(DatasetId.harmonized_nrt, Sensor.harmonized, Product.nrt, _HARMONIZED),
        Dataset(DatasetId.harmonized_sp, Sensor.harmonized, Product.sp, _HARMONIZED),
    )
}


class DatasetSchemaError(RuntimeError):
    """A dataset's relation lacks a required column."""


# -------------------------------------------------------------- connection ---

_con: duckdb.DuckDBPyConnection | None = None
_attached = False
_attach_lock = threading.Lock()


def connect() -> None:
    """Open the shared in-memory database, load the extensions and attach the lake."""
    global _con, _attached
    _con = duckdb.connect()
    _con.execute(
        "INSTALL spatial; LOAD spatial; INSTALL h3 FROM community; LOAD h3; "
        "INSTALL ducklake; LOAD ducklake; INSTALL sqlite; LOAD sqlite;"
    )
    _attached = False
    _ensure_attached()


def _catalog_initialized(catalog: str) -> bool:
    """Whether DuckLake has created its tables in the catalog yet.

    The ETL creates an empty SQLite file (to set WAL mode) before its first
    write; a READ_ONLY attach refuses that file, so wait until it's initialized.
    """
    if not os.path.exists(catalog):
        return False
    con = sqlite3.connect(f"file:{catalog}?mode=ro", uri=True)
    try:
        return (
            con.execute("select 1 from sqlite_master where type = 'table' and name = 'ducklake_metadata'").fetchone()
            is not None
        )
    finally:
        con.close()


def _ensure_attached() -> bool:
    """Attach the lake once its catalog exists (the ETL may not have run yet).

    READ_ONLY: the backend never writes. The catalog stores the data path the
    ETL used (inside the Airflow containers); OVERRIDE_DATA_PATH points it to
    where the same files are seen from here.
    """
    global _attached
    if _attached:
        return True
    catalog = f"{lake_path()}/catalog.sqlite"
    if not _catalog_initialized(catalog):
        return False
    with _attach_lock:
        if not _attached:
            _con.execute(
                f"ATTACH {_sql_str('ducklake:sqlite:' + catalog)} AS {LAKE_ALIAS} "
                f"(READ_ONLY, DATA_PATH {_sql_str(lake_path() + '/files/')}, OVERRIDE_DATA_PATH true)"
            )
            _attached = True
    return True


def close() -> None:
    global _con, _attached
    if _con is not None:
        _con.close()
        _con = None
    _attached = False


def get_cursor() -> Iterator[duckdb.DuckDBPyConnection]:
    """FastAPI dependency: one cursor per request (thread-safe over the shared DB)."""
    if _con is None:
        raise RuntimeError("database not connected")
    _ensure_attached()
    cur = _con.cursor()
    try:
        yield cur
    finally:
        cur.close()


# ------------------------------------------------------------------ queries ---


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(cur: duckdb.DuckDBPyConnection) -> set[str]:
    rows = cur.execute(
        "select table_name from information_schema.tables where table_catalog = ? and table_schema = ?",
        [LAKE_ALIAS, GOLD_SCHEMA],
    ).fetchall()
    return {r[0] for r in rows}


def exists(cur: duckdb.DuckDBPyConnection, ds: Dataset) -> bool:
    """Whether the dataset has data in the lake (False if there is no lake yet)."""
    if not _attached:
        return False
    tables = _tables(cur)
    if not ds.on_hexagon_model:
        return str(ds.id) in tables
    if not all(t in tables for t in HEXAGON_TABLES):
        return False
    return cur.execute(f"select exists (select 1 from {DAILY} where {ds.predicate()})").fetchone()[0]


def hexagon_summaries(cur: duckdb.DuckDBPyConnection) -> dict[DatasetId, tuple[int, date, date]]:
    """(fire detections, first date, last date) of every MODIS/VIIRS dataset with data,
    in one pass over the daily metrics (instead of one query per dataset)."""
    if not _attached or not all(t in _tables(cur) for t in HEXAGON_TABLES):
        return {}
    rows = cur.execute(
        f"select sensor, product, is_preferred, sum(fire_detections), min(acq_date), max(acq_date) "
        f"from {DAILY} group by all"
    ).fetchall()
    out: dict[DatasetId, tuple[int, date, date]] = {}

    def add(ds_id: str, n, first, last):
        key = DatasetId(ds_id)
        if key in out:
            m, f, l = out[key]
            n, first, last = m + n, min(f, first), max(l, last)
        out[key] = (int(n or 0), first, last)

    for sensor, product, preferred, n, first, last in rows:
        if sensor not in ("modis", "viirs") or product not in ("sp", "nrt"):
            continue
        add(f"{sensor}_{product}", n, first, last)
        if preferred:
            add(f"{sensor}_best", n, first, last)
    return out


def summary(cur: duckdb.DuckDBPyConnection, ds: Dataset) -> tuple[int, date | None, date | None]:
    """(fire detections, first date, last date) of a dataset that exists."""
    if ds.on_hexagon_model:
        query = f"select sum(fire_detections), min(acq_date), max(acq_date) from {DAILY} where {ds.predicate()}"
    else:
        query = f"select count(*), min(acq_date), max(acq_date) from {points_relation(cur, ds)}"
    n, first, last = cur.execute(query).fetchone()
    return int(n or 0), first, last


def points_relation(cur: duckdb.DuckDBPyConnection, ds: Dataset) -> str:
    """One row per detection, same columns for every dataset:

    latitude, longitude, acq_date, acq_datetime_utc, frp, daynight, satellite,
    confidence (text), brightness, h3_r6, product, is_static_source.
    """
    if ds.on_hexagon_model:
        return (
            "(select latitude, longitude, acq_date, acq_datetime_utc, frp, daynight, satellite, "
            "confidence_raw as confidence, brightness, h3_r6, product, is_static_source "
            f"from {DETECTIONS} where {ds.predicate()})"
        )
    return _harmonized_points(cur, ds)


def cells_relation(cur: duckdb.DuckDBPyConnection, ds: Dataset, with_center: bool = False) -> str:
    """Hexagon rows: h3_r6, acq_date, daynight, detections (fire), static_detections,
    frp_sum (fire). Several rows per hexagon-day are possible (one per satellite and
    product): endpoints aggregate them.

    with_center adds latitude/longitude = the hexagon's center (from dim_hexagon), the
    point spatial filters test. Only join when a bbox/zone needs it: it's the expensive part.
    """
    if ds.on_hexagon_model:
        if not with_center:
            return (
                "(select h3_r6, acq_date, daynight, fire_detections as detections, static_detections, "
                f"fire_frp_sum as frp_sum from {DAILY} where {ds.predicate()})"
            )
        return (
            "(select d.h3_r6, h.center_lat as latitude, h.center_lon as longitude, d.acq_date, d.daynight, "
            "d.fire_detections as detections, d.static_detections, d.fire_frp_sum as frp_sum "
            f"from {DAILY} d join {HEXAGONS} h using (h3_r6) where {ds.predicate('d')})"
        )
    points = _harmonized_points(cur, ds)
    return (
        "(select h3_r6, h3_cell_to_latlng(h3_r6)[1] as latitude, h3_cell_to_latlng(h3_r6)[2] as longitude, "
        "acq_date, daynight, count(*) as detections, 0 as static_detections, sum(frp) as frp_sum "
        f"from {points} where h3_r6 is not null group by h3_r6, acq_date, daynight)"
    )


def _harmonized_points(cur: duckdb.DuckDBPyConnection, ds: Dataset) -> str:
    """Points of a harmonized dataset, from its own relation (contract in the README)."""
    source = ds.relation
    present = {row[0] for row in cur.execute(f"describe {source}").fetchall()}

    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    if missing:
        raise DatasetSchemaError(f"dataset {ds.id} is missing required columns: {', '.join(missing)}")

    optional = []
    for prop in OPTIONAL_PROPERTIES:
        col = (ds.columns or {}).get(prop)
        if col in present:
            expr = f"cast({_ident(col)} as varchar)" if prop in ("satellite", "confidence") else f"cast({_ident(col)} as double)"
        else:
            expr = f"cast(null as {'double' if prop == 'brightness' else 'varchar'})"
        optional.append(f"{expr} as {prop}")

    if H3_COLUMN in present:
        h3 = f"cast({_ident(H3_COLUMN)} as ubigint)"
    else:
        h3 = (
            f"case when latitude between -90 and 90 and longitude between -180 and 180 "
            f"then h3_latlng_to_cell(latitude, longitude, {H3_RES}) end"
        )
    optional.append(f"{h3} as {H3_COLUMN}")

    return (
        f"(select {', '.join(REQUIRED_COLUMNS)}, {', '.join(optional)}, "
        f"'{ds.product}' as product, false as is_static_source "
        f"from {source} where latitude is not null)"
    )


@dataclass(frozen=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


@dataclass(frozen=True)
class Zone:
    """A user-drawn area: GeoJSON (Multi)Polygon text plus its envelope."""

    geojson: str
    envelope: BBox


@dataclass(frozen=True)
class Filters:
    start: date | None = None
    end: date | None = None
    bbox: BBox | None = None
    zone: Zone | None = None
    daynight: str | None = None

    def where(self) -> tuple[str, list]:
        """SQL WHERE clause (always valid, `true` when empty) and its parameters."""
        clauses: list[str] = ["true"]
        params: list = []
        if self.start:
            clauses.append("acq_date >= ?")
            params.append(self.start)
        if self.end:
            clauses.append("acq_date <= ?")
            params.append(self.end)
        # The BETWEENs let DuckDB skip Parquet row groups; ST_Intersects is the actual test.
        if self.bbox:
            b = self.bbox
            clauses.append("longitude between ? and ? and latitude between ? and ?")
            params += [b.min_lon, b.max_lon, b.min_lat, b.max_lat]
            clauses.append("ST_Intersects(ST_Point(longitude, latitude), ST_MakeEnvelope(?, ?, ?, ?))")
            params += [b.min_lon, b.min_lat, b.max_lon, b.max_lat]
        if self.zone:
            e = self.zone.envelope
            clauses.append("longitude between ? and ? and latitude between ? and ?")
            params += [e.min_lon, e.max_lon, e.min_lat, e.max_lat]
            clauses.append("ST_Intersects(ST_Point(longitude, latitude), ST_GeomFromGeoJSON(?))")
            params.append(self.zone.geojson)
        if self.daynight:
            clauses.append("daynight = ?")
            params.append(self.daynight)
        return " and ".join(clauses), params
