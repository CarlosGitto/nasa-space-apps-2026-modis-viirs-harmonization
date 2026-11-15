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
MONTHLY = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.fct_hexagon_monthly"
PROFILE = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.fct_hexagon_profile"
HEXAGON_TABLES = ("fct_hexagon_detections", "fct_hexagon_daily", "dim_hexagon")
# Weather (NASA Earthdata), keyed by grid cell and UTC day: see data/README.md.
WEATHER = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.fct_weather_daily"  # MERRA-2
PRECIPITATION = f"{LAKE_ALIAS}.{GOLD_SCHEMA}.fct_precipitation_daily"  # GPM IMERG

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


# --------------------------------------------------------------- one hexagon ---
# Everything below reads rows of ONE hexagon from tables sorted by h3_r6, so a
# lookup only touches the files that hold it: the products are precomputed in
# gold, the API just hands them over.


class InvalidHexagon(ValueError):
    """Not a valid H3 resolution-6 cell id."""


def hexagon_key(cur: duckdb.DuckDBPyConnection, h3_id: str) -> int:
    """The UBIGINT key of a resolution-6 H3 id given as text (e.g. 86c2e3117ffffff)."""
    valid, res, key = cur.execute(
        "select h3_is_valid_cell(?), "
        "case when h3_is_valid_cell(?) then h3_get_resolution(?) end, "
        "case when h3_is_valid_cell(?) then h3_string_to_h3(?) end",
        [h3_id] * 5,
    ).fetchone()
    if not valid or res != H3_RES:
        raise InvalidHexagon(f"{h3_id} is not a valid H3 resolution-{H3_RES} cell")
    return key


def hexagon_tables_ready(cur: duckdb.DuckDBPyConnection) -> bool:
    """Whether the lake has the hexagon gold products (catalog lookup, no data scan)."""
    if not _attached:
        return False
    names = _tables(cur)
    return all(t in names for t in (*HEXAGON_TABLES, "fct_hexagon_monthly", "fct_hexagon_profile"))


def hexagon_identity(cur: duckdb.DuckDBPyConnection, key: int) -> tuple | None:
    """dim_hexagon row: id, center, area, boundary (GeoJSON), parents, dates, static flag."""
    return cur.execute(
        f"""
        select h3_id, center_lon, center_lat, area_km2, ST_AsGeoJSON(ST_GeomFromText(boundary_wkt)),
               h3_h3_to_string(h3_r5), h3_h3_to_string(h3_r4), h3_h3_to_string(h3_r3),
               first_date, last_date, has_static_source
        from {HEXAGONS} where h3_r6 = ?
        """,
        [key],
    ).fetchone()


def hexagon_profiles(cur: duckdb.DuckDBPyConnection, key: int) -> list[dict]:
    """fct_hexagon_profile rows (one per sensor) as dicts."""
    rel = cur.execute(f"select * exclude (h3_r6) from {PROFILE} where h3_r6 = ? order by sensor", [key])
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, row)) for row in rel.fetchall()]


def hexagon_monthly(cur: duckdb.DuckDBPyConnection, key: int) -> list[dict]:
    rel = cur.execute(
        f"select * exclude (h3_r6, last_load_id) from {MONTHLY} where h3_r6 = ? order by acq_month, sensor, satellite",
        [key],
    )
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, row)) for row in rel.fetchall()]


def hexagon_period(cur: duckdb.DuckDBPyConnection, key: int, start: date | None, end: date | None) -> list[tuple]:
    """Preferred daily rows of one hexagon in [start, end]: (sensor, satellite, date, daynight,
    fire, frp_sum, frp_max, high_conf, static)."""
    clauses, params = ["h3_r6 = ?", "is_preferred"], [key]
    if start:
        clauses.append("acq_date >= ?")
        params.append(start)
    if end:
        clauses.append("acq_date <= ?")
        params.append(end)
    return cur.execute(
        f"""
        select sensor, satellite, acq_date, daynight, fire_detections, fire_frp_sum, fire_frp_max,
               fire_high_conf, static_detections
        from {DAILY} where {" and ".join(clauses)}
        order by acq_date
        """,
        params,
    ).fetchall()


DETECTION_COLUMNS = (
    "acq_datetime_utc", "solar_hour_local", "sensor", "satellite", "product", "instrument", "version",
    "latitude", "longitude", "frp", "confidence_raw", "confidence_level", "daynight",
    "brightness", "brightness_aux", "scan", "track", "fire_type", "is_static_source", "is_preferred",
)


def hexagon_detections(
    cur: duckdb.DuckDBPyConnection, key: int, ds: Dataset, start: date | None, end: date | None, limit: int
) -> list[dict]:
    """Detections of one hexagon for a dataset and date range, most recent first (limit + 1 rows)."""
    clauses, params = ["h3_r6 = ?", ds.predicate()], [key]
    if start:
        clauses.append("acq_date >= ?")
        params.append(start)
    if end:
        clauses.append("acq_date <= ?")
        params.append(end)
    rel = cur.execute(
        f"select {', '.join(DETECTION_COLUMNS)} from {DETECTIONS} where {' and '.join(clauses)} "
        "order by acq_datetime_utc desc limit ?",
        [*params, limit + 1],
    )
    return [dict(zip(DETECTION_COLUMNS, row)) for row in rel.fetchall()]


# ------------------------------------------------- a selection of hexagons ---
# A drawn area or a multi-selection: many res-6 hexagons at once, empty or not.

MAX_SELECTION = 5000


def hexagon_keys(cur: duckdb.DuckDBPyConnection, ids: list[str]) -> list[int]:
    """UBIGINT keys of res-6 H3 ids (duplicates dropped); InvalidHexagon on the first bad id."""
    rows = cur.execute(
        "select id, case when h3_is_valid_cell(id) and h3_get_resolution(id) = ? then h3_string_to_h3(id) end "
        "from unnest(?::varchar[]) with ordinality t(id, n) order by n",
        [H3_RES, ids],
    ).fetchall()
    keys = []
    for h3_id, key in rows:
        if key is None:
            raise InvalidHexagon(f"{h3_id} is not a valid H3 resolution-{H3_RES} cell")
        keys.append(key)
    return list(dict.fromkeys(keys))


def _key_list(keys: list[int]) -> str:
    # Integers we produced ourselves: safe as SQL literals, and literals let DuckLake
    # skip the files whose h3_r6 min/max can't contain them.
    return ", ".join(str(int(k)) for k in keys)


def _date_clauses(start: date | None, end: date | None) -> tuple[list[str], list]:
    clauses, params = [], []
    if start:
        clauses.append("acq_date >= ?")
        params.append(start)
    if end:
        clauses.append("acq_date <= ?")
        params.append(end)
    return clauses, params


def selection_summary(
    cur: duckdb.DuckDBPyConnection, keys: list[int], start: date | None, end: date | None
) -> list[tuple]:
    """Per hexagon with any history: (h3, modis_total, viirs_total, last_date,
    modis_period, viirs_period, static_period). Continuous series; never adds sensors."""
    in_keys = _key_list(keys)
    dates, params = _date_clauses(start, end)
    period_where = " and ".join(["is_preferred", f"h3_r6 in ({in_keys})", *dates])
    return cur.execute(
        f"""
        with hist as (
            select h3_r6,
                   coalesce(sum(fire_detections) filter (where sensor = 'modis'), 0) as modis_total,
                   coalesce(sum(fire_detections) filter (where sensor = 'viirs'), 0) as viirs_total,
                   max(last_date) as last_date
            from {PROFILE} where h3_r6 in ({in_keys})
            group by h3_r6
        ),
        period as (
            select h3_r6,
                   coalesce(sum(fire_detections) filter (where sensor = 'modis'), 0) as modis_period,
                   coalesce(sum(fire_detections) filter (where sensor = 'viirs'), 0) as viirs_period,
                   sum(static_detections) as static_period
            from {DAILY} where {period_where}
            group by h3_r6
        )
        select h3_h3_to_string(h3_r6), modis_total, viirs_total, last_date,
               coalesce(modis_period, 0), coalesce(viirs_period, 0), coalesce(static_period, 0)
        from hist left join period using (h3_r6)
        order by coalesce(modis_period, 0) + coalesce(viirs_period, 0) desc, modis_total + viirs_total desc
        """,
        params,
    ).fetchall()


def selection_detections(
    cur: duckdb.DuckDBPyConnection, keys: list[int], ds: Dataset, start: date | None, end: date | None, limit: int
) -> list[dict]:
    """Detections of many hexagons for a dataset and date range, most recent first (limit + 1 rows)."""
    dates, params = _date_clauses(start, end)
    where = " and ".join([f"h3_r6 in ({_key_list(keys)})", ds.predicate(), *dates])
    rel = cur.execute(
        f"select h3_h3_to_string(h3_r6) as h3, {', '.join(DETECTION_COLUMNS)} from {DETECTIONS} where {where} "
        "order by acq_datetime_utc desc limit ?",
        [*params, limit + 1],
    )
    return [dict(zip(("h3", *DETECTION_COLUMNS), row)) for row in rel.fetchall()]


# ------------------------------------------------------------------ weather ---
# Weather gold is keyed by grid cell (its center) and UTC day, not by hexagon: a
# hexagon takes the cell of each grid whose center is nearest to its own.

# A day with at least this much precipitation (mm) counts as a rain day.
RAIN_DAY_MM = 1.0
# Half-width (degrees) of the box searched for the nearest cell: wider than half
# the coarsest grid cell (MERRA-2, 0.5° x 0.625°), so the nearest is always inside.
CELL_SEARCH_DEG = 0.5

WEATHER_COLUMNS = (
    "t2m_max_c", "t2m_min_c", "t2m_mean_c", "dewpoint_mean_c", "rh_min_pct", "rh_mean_pct",
    "vpd_max_kpa", "vpd_mean_kpa", "wind_speed_max_ms", "wind_speed_mean_ms", "wind_dir_mean_deg", "hours",
)


def weather_tables(cur: duckdb.DuckDBPyConnection) -> set[str]:
    """Which weather gold relations the lake has (catalog lookup, no data scan)."""
    if not _attached:
        return set()
    return {WEATHER, PRECIPITATION} & {f"{LAKE_ALIAS}.{GOLD_SCHEMA}.{t}" for t in _tables(cur)}


def hexagon_center(cur: duckdb.DuckDBPyConnection, key: int) -> tuple[float, float]:
    """(lat, lon) of a hexagon's center: computed by H3, the hexagon needn't have detections."""
    lat, lon = cur.execute("select h3_cell_to_latlng(?)[1], h3_cell_to_latlng(?)[2]", [key, key]).fetchone()
    return lat, lon


def nearest_cell(cur: duckdb.DuckDBPyConnection, table: str, lat: float, lon: float) -> tuple[float, float, float] | None:
    """(cell_lat, cell_lon, distance_km) of the cell of `table` nearest to a point, or None
    if the lake has no cell around it (outside the loaded area).

    The BETWEENs let DuckDB skip files by their min/max statistics; the ordering is
    an equirectangular distance, exact enough at this scale.
    """
    d = CELL_SEARCH_DEG
    return cur.execute(
        f"""
        select cell_lat, cell_lon,
               111.2 * sqrt(power(cell_lat - ?, 2) + power((cell_lon - ?) * cos(radians(?)), 2)) as km
        from (select distinct cell_lat, cell_lon from {table}
              where cell_lat between ? and ? and cell_lon between ? and ?)
        order by km
        limit 1
        """,
        [lat, lon, lat, lat - d, lat + d, lon - d, lon + d],
    ).fetchone()


def weather_last_date(cur: duckdb.DuckDBPyConnection, cells: dict[str, tuple[float, float]]) -> date | None:
    """Last day with data in any of the cells ({table: (cell_lat, cell_lon)})."""
    last = [
        cur.execute(f"select max(obs_date) from {t} where cell_lat = ? and cell_lon = ?", list(c)).fetchone()[0]
        for t, c in cells.items()
    ]
    return max((d for d in last if d), default=None)


def weather_days(cur: duckdb.DuckDBPyConnection, cell: tuple[float, float], start: date, end: date) -> dict[date, dict]:
    """MERRA-2 daily rows of one cell in [start, end], by date."""
    rel = cur.execute(
        f"select obs_date, {', '.join(WEATHER_COLUMNS)} from {WEATHER} "
        "where cell_lat = ? and cell_lon = ? and obs_date between ? and ? order by obs_date",
        [*cell, start, end],
    )
    return {row[0]: dict(zip(WEATHER_COLUMNS, row[1:])) for row in rel.fetchall()}


def precipitation_days(
    cur: duckdb.DuckDBPyConnection, cell: tuple[float, float], start: date, end: date
) -> dict[date, dict]:
    """IMERG daily rows of one cell in [start, end], by date, with the accumulations:
    precipitation over the last 7 / 30 days (that day included) and days since the
    last rain day (>= RAIN_DAY_MM; null if none in the cell's history).

    Computed over the cell's whole history up to `end` (hundreds of rows), so the
    first days of the range see the rain before it. Missing days count as no rain.
    """
    rel = cur.execute(
        f"""
        with series as (
            select obs_date, precipitation_mm, imerg_run,
                   sum(precipitation_mm) over (order by obs_date range between interval 6 days preceding and current row) as p7,
                   sum(precipitation_mm) over (order by obs_date range between interval 29 days preceding and current row) as p30,
                   max(obs_date) filter (where precipitation_mm >= ?) over (order by obs_date) as last_rain
            from {PRECIPITATION}
            where cell_lat = ? and cell_lon = ? and obs_date <= ?
        )
        select obs_date, precipitation_mm, p7, p30, obs_date - last_rain, imerg_run
        from series where obs_date >= ?
        order by obs_date
        """,
        [RAIN_DAY_MM, *cell, end, start],
    )
    cols = ("precipitation_mm", "precipitation_7d_mm", "precipitation_30d_mm", "days_since_rain", "imerg_run")
    return {row[0]: dict(zip(cols, row[1:])) for row in rel.fetchall()}
