"""DuckDB access to the gold layer of the lake.

The lake (data/lake at the repo root) is a DuckLake written by the ETL: a SQLite
catalog (`catalog.sqlite`) plus Parquet files (`files/`). This module attaches
it read-only as `lake` and queries `lake.gold.<dataset>` (tables or views).
Every query reads the latest committed snapshot, so new ETL runs show up
immediately, and writes are atomic, so a half-written table is never seen.

Datasets are the extension point for MODIS, VIIRS and (later) harmonized data:
each one is a relation `gold.<sensor>_<product>` plus a mapping from the API's
optional properties to its columns. Endpoints never look at sensor specifics,
so adding or adjusting a dataset only touches DATASETS below.
"""

from __future__ import annotations

import os
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


class DatasetId(StrEnum):
    modis_nrt = "modis_nrt"
    modis_sp = "modis_sp"
    viirs_nrt = "viirs_nrt"
    viirs_sp = "viirs_sp"
    harmonized_nrt = "harmonized_nrt"
    harmonized_sp = "harmonized_sp"


LAKE_ALIAS = "lake"
GOLD_SCHEMA = "gold"

# Every dataset must have these columns (same names in the lake).
REQUIRED_COLUMNS = ("latitude", "longitude", "acq_date", "acq_datetime_utc", "frp", "daynight")
# API property -> lake column. Missing columns are returned as null.
OPTIONAL_PROPERTIES = ("satellite", "confidence", "brightness")


@dataclass(frozen=True)
class Dataset:
    id: DatasetId
    sensor: Sensor
    product: Product
    columns: dict[str, str]  # optional property -> lake column

    @property
    def relation(self) -> str:
        return f'{LAKE_ALIAS}.{GOLD_SCHEMA}."{self.id}"'


_MODIS = {"satellite": "satellite", "confidence": "confidence", "brightness": "brightness"}
_VIIRS = {"satellite": "satellite", "confidence": "confidence", "brightness": "bright_ti4"}
# Provisional: adjust once the harmonization team defines its output columns.
_HARMONIZED = {"satellite": "satellite", "confidence": "confidence", "brightness": "brightness"}

DATASETS: dict[DatasetId, Dataset] = {
    d.id: d
    for d in (
        Dataset(DatasetId.modis_nrt, Sensor.modis, Product.nrt, _MODIS),
        Dataset(DatasetId.modis_sp, Sensor.modis, Product.sp, _MODIS),
        Dataset(DatasetId.viirs_nrt, Sensor.viirs, Product.nrt, _VIIRS),
        Dataset(DatasetId.viirs_sp, Sensor.viirs, Product.sp, _VIIRS),
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
    if not os.path.exists(catalog):
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


def exists(cur: duckdb.DuckDBPyConnection, ds: Dataset) -> bool:
    """Whether the dataset's table or view is in the lake (False if there is no lake yet)."""
    if not _attached:
        return False
    return (
        cur.execute(
            "select count(*) from information_schema.tables "
            "where table_catalog = ? and table_schema = ? and table_name = ?",
            [LAKE_ALIAS, GOLD_SCHEMA, str(ds.id)],
        ).fetchone()[0]
        > 0
    )


def base_relation(cur: duckdb.DuckDBPyConnection, ds: Dataset) -> str:
    """Subquery with the common columns of a dataset, whatever its sensor.

    Columns: the required ones plus satellite / confidence (as text) / brightness.
    """
    source = ds.relation
    present = {row[0] for row in cur.execute(f"describe {source}").fetchall()}

    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    if missing:
        raise DatasetSchemaError(f"dataset {ds.id} is missing required columns: {', '.join(missing)}")

    optional = []
    for prop in OPTIONAL_PROPERTIES:
        col = ds.columns.get(prop)
        if col in present:
            expr = f"cast({_ident(col)} as varchar)" if prop in ("satellite", "confidence") else f"cast({_ident(col)} as double)"
        else:
            expr = f"cast(null as {'double' if prop == 'brightness' else 'varchar'})"
        optional.append(f"{expr} as {prop}")

    return (
        f"(select {', '.join(REQUIRED_COLUMNS)}, {', '.join(optional)} "
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
