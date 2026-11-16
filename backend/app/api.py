"""HTTP endpoints. Same contract for every dataset (MODIS, VIIRS, harmonized).

`detections` counts are wildfires; heat from static sources (refineries, gas
flares, industry, volcanoes) is reported apart as `static_detections`.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from app import db, power
from app.db import DATASETS, BBox, Dataset, DatasetId, Filters, Product, Sensor, Zone

router = APIRouter()

Cursor = Annotated[duckdb.DuckDBPyConnection, Depends(db.get_cursor)]


# ------------------------------------------------------------------ models ---


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class DatasetInfo(BaseModel):
    id: DatasetId
    sensor: Sensor
    product: Product  # sp | nrt | best (SP where published, NRT after it)
    available: bool
    rows: int  # wildfire detections (static sources excluded)
    start_date: date | None
    end_date: date | None


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # lon, lat


class DetectionProperties(BaseModel):
    sensor: Sensor
    product: Product  # where this detection comes from: sp or nrt
    is_static_source: bool  # heat that is not a wildfire (refinery, gas flare, volcano...)
    satellite: str | None
    acq_datetime_utc: datetime
    frp: float | None
    confidence: str | None  # MODIS 0-100, VIIRS l/n/h: different scales, not harmonized
    daynight: str | None
    brightness: float | None  # Kelvin: MODIS brightness, VIIRS bright_ti4


class DetectionFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: DetectionProperties


class DetectionCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[DetectionFeature]
    truncated: bool  # true when more detections matched than `limit`


# detections are fractional on the harmonized datasets (a VIIRS detection = 1 / k MODIS-sized ones)
class DailyStat(BaseModel):
    date: date
    detections: float
    day: float
    night: float
    frp_sum: float | None
    frp_mean: float | None
    static_detections: int


class MonthlyStat(BaseModel):
    year: int
    month: int  # 1-12
    detections: float
    frp_sum: float | None
    static_detections: int


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[tuple[float, float]]]


class CellProperties(BaseModel):
    detections: float
    frp_sum: float | None
    static_detections: int


class CellFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: PolygonGeometry
    properties: CellProperties


class GridCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    cell: float
    features: list[CellFeature]


class HexProperties(BaseModel):
    h3: str  # H3 cell index
    detections: float
    frp_sum: float | None
    static_detections: int


class HexFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: PolygonGeometry
    properties: HexProperties


class HexCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    resolution: int
    features: list[HexFeature]


# ------------------------------------------------------------ dependencies ---

MAX_ZONE_CHARS = 10_000


def parse_filters(
    start: Annotated[date | None, Query(description="First acquisition date (inclusive)")] = None,
    end: Annotated[date | None, Query(description="Last acquisition date (inclusive)")] = None,
    bbox: Annotated[
        str | None,
        Query(description="minLon,minLat,maxLon,maxLat (WGS84)", examples=["-74,-56,-53,-21"]),
    ] = None,
    zone: Annotated[
        str | None,
        Query(
            description="GeoJSON Polygon or MultiPolygon geometry (WGS84), e.g. a user-drawn area. "
            "Omit it for the whole world.",
            max_length=MAX_ZONE_CHARS,
        ),
    ] = None,
    daynight: Annotated[Literal["D", "N"] | None, Query(description="D = day, N = night")] = None,
) -> Filters:
    if start and end and end < start:
        raise HTTPException(422, "end must be on or after start")
    return Filters(
        start=start,
        end=end,
        bbox=_parse_bbox(bbox) if bbox else None,
        zone=_parse_zone(zone) if zone else None,
        daynight=daynight,
    )


def _parse_bbox(raw: str) -> BBox:
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in raw.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat") from None
    if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
        raise HTTPException(422, "bbox out of range or min >= max")
    return BBox(min_lon, min_lat, max_lon, max_lat)


def _parse_zone(raw: str) -> Zone:
    """Validate a GeoJSON (Multi)Polygon and compute its envelope."""
    try:
        geom = json.loads(raw)
        if geom.get("type") == "Polygon":
            polygons = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            polygons = geom["coordinates"]
        else:
            raise ValueError
        points = [(float(x), float(y)) for poly in polygons for ring in poly for x, y, *_ in ring]
        if any(len(ring) < 4 for poly in polygons for ring in poly):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(422, "zone must be a GeoJSON Polygon or MultiPolygon geometry") from None
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    if not (-180 <= min(lons) and max(lons) <= 180 and -90 <= min(lats) and max(lats) <= 90):
        raise HTTPException(422, "zone coordinates out of range")
    return Zone(geojson=json.dumps(geom), envelope=BBox(min(lons), min(lats), max(lons), max(lats)))


FiltersDep = Annotated[Filters, Depends(parse_filters)]


def _dataset(cur: duckdb.DuckDBPyConnection, dataset: DatasetId) -> Dataset:
    """The dataset, or 404 if nothing has landed for it yet."""
    ds = DATASETS[dataset]
    if not db.exists(cur, ds):
        raise HTTPException(404, f"no data yet for {dataset}")
    return ds


def _relation(build, cur: duckdb.DuckDBPyConnection, ds: Dataset, **kwargs) -> str:
    try:
        return build(cur, ds, **kwargs)
    except db.DatasetSchemaError as e:
        raise HTTPException(500, str(e)) from None


def _cells(cur: duckdb.DuckDBPyConnection, dataset: DatasetId, filters: Filters) -> str:
    """Hexagon rows of a dataset; with the hexagon centers only if a spatial filter needs them."""
    spatial = filters.bbox is not None or filters.zone is not None
    return _relation(db.cells_relation, cur, _dataset(cur, dataset), with_center=spatial)


# --------------------------------------------------------------- endpoints ---


@router.get("/health")
def health() -> Health:
    return Health()


@router.get("/datasets")
def list_datasets(cur: Cursor) -> list[DatasetInfo]:
    """Every dataset the API knows about, with what is currently in the lake."""
    hexagon = db.hexagon_summaries(cur)  # all MODIS/VIIRS datasets in one pass
    out = []
    for ds in DATASETS.values():
        rows, start, end = 0, None, None
        if ds.on_hexagon_model:
            available = ds.id in hexagon
            if available:
                rows, start, end = hexagon[ds.id]
        else:
            available = db.exists(cur, ds)
            if available:
                try:
                    rows, start, end = db.summary(cur, ds)
                except db.DatasetSchemaError:
                    available = False
        out.append(
            DatasetInfo(
                id=ds.id, sensor=ds.sensor, product=ds.product,
                available=available, rows=rows, start_date=start, end_date=end,
            )
        )
    return out


@router.get("/datasets/{dataset}/detections")
def detections(
    dataset: DatasetId,
    cur: Cursor,
    filters: FiltersDep,
    limit: Annotated[int, Query(ge=1, le=50_000)] = 5_000,
) -> DetectionCollection:
    """Detections as GeoJSON points, most recent first (static sources flagged, not removed)."""
    ds = _dataset(cur, dataset)
    base = _relation(db.points_relation, cur, ds)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select ST_AsGeoJSON(ST_Point(longitude, latitude)) as geometry,
               satellite, acq_datetime_utc, frp, confidence, daynight, brightness, product, is_static_source
        from {base}
        where {where}
        order by acq_datetime_utc desc
        limit ?
        """,
        [*params, limit + 1],
    ).fetchall()

    features = [
        DetectionFeature(
            geometry=json.loads(geometry),
            properties=DetectionProperties(
                sensor=ds.sensor, product=product, is_static_source=is_static, satellite=satellite,
                acq_datetime_utc=acq_datetime_utc, frp=frp, confidence=confidence, daynight=daynight,
                brightness=brightness,
            ),
        )
        for geometry, satellite, acq_datetime_utc, frp, confidence, daynight, brightness, product, is_static
        in rows[:limit]
    ]
    return DetectionCollection(features=features, truncated=len(rows) > limit)


CSV_MAX_ROWS = 1_000_000
CSV_COLUMNS = (
    "latitude", "longitude", "acq_date", "acq_datetime_utc", "satellite", "product", "confidence", "daynight",
    "frp", "brightness", "is_static_source", "h3_r6",
)


@router.get("/datasets/{dataset}/detections.csv")
def detections_csv(
    dataset: DatasetId,
    cur: Cursor,
    filters: FiltersDep,
    limit: Annotated[int, Query(ge=1, le=CSV_MAX_ROWS)] = CSV_MAX_ROWS,
) -> Response:
    """Every detection matching the filters as a CSV download (the hotspots of a calendar search), oldest first.
    Static sources are kept and flagged. `X-Truncated: true` when more matched than `limit`."""
    ds = _dataset(cur, dataset)
    base = _relation(db.points_relation, cur, ds)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select latitude, longitude, acq_date, acq_datetime_utc, satellite, product, confidence, daynight,
               frp, brightness, is_static_source, h3_h3_to_string(h3_r6)
        from {base}
        where {where}
        order by acq_datetime_utc
        limit ?
        """,
        [*params, limit + 1],
    ).fetchall()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(rows[:limit])
    span = "_".join(str(d) for d in (filters.start, filters.end) if d) or "all"
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="hotspots_{dataset}_{span}.csv"',
            "X-Truncated": "true" if len(rows) > limit else "false",
        },
    )


@router.get("/datasets/{dataset}/stats/daily")
def daily_stats(dataset: DatasetId, cur: Cursor, filters: FiltersDep) -> list[DailyStat]:
    """Detections and FRP per acquisition date."""
    return _daily(cur, _cells(cur, dataset, filters), filters)


@router.get("/stats/harmonized/daily")
def harmonized_daily_stats(cur: Cursor, filters: FiltersDep) -> list[DailyStat]:
    """Daily calendar of the harmonized series (MODIS before 2012-02, VIIRS S-NPP / k after), in MODIS units.
    Detections are fractional. Same shape as /datasets/{dataset}/stats/daily."""
    if not db.harmonized_series_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    base = db.harmonized_series_relation(with_center=filters.bbox is not None or filters.zone is not None)
    return _daily(cur, base, filters)


def _daily(cur: duckdb.DuckDBPyConnection, base: str, filters: Filters) -> list[DailyStat]:
    """Rows of `base` (acq_date, daynight, detections, frp_sum, static_detections) summed per date."""
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select acq_date, sum(detections),
               coalesce(sum(detections) filter (where daynight = 'D'), 0),
               coalesce(sum(detections) filter (where daynight = 'N'), 0),
               sum(frp_sum), sum(frp_sum) / nullif(sum(detections), 0),
               sum(static_detections)
        from {base}
        where {where}
        group by acq_date
        order by acq_date
        """,
        params,
    ).fetchall()
    return [
        DailyStat(date=d, detections=n, day=day, night=night, frp_sum=fs, frp_mean=fm, static_detections=st)
        for d, n, day, night, fs, fm, st in rows
    ]


@router.get("/datasets/{dataset}/stats/grid")
def grid_stats(
    dataset: DatasetId,
    cur: Cursor,
    filters: FiltersDep,
    cell: Annotated[float, Query(ge=0.1, le=10, description="Cell size in degrees")] = 1.0,
) -> GridCollection:
    """Detections aggregated on a lat/lon grid, as GeoJSON polygons (for heatmaps)."""
    base = _relation(db.points_relation, cur, _dataset(cur, dataset))
    where, params = filters.where()
    rows = cur.execute(
        f"""
        with cells as (
            select floor(longitude / ?) * ? as x0, floor(latitude / ?) * ? as y0,
                   count(*) filter (where not is_static_source) as detections,
                   sum(frp) filter (where not is_static_source) as frp_sum,
                   count(*) filter (where is_static_source) as static_detections
            from {base}
            where {where}
            group by all
        )
        select ST_AsGeoJSON(ST_MakeEnvelope(x0, y0, least(x0 + ?, 180), least(y0 + ?, 90))),
               detections, frp_sum, static_detections
        from cells
        order by detections desc
        """,
        [cell, cell, cell, cell, *params, cell, cell],
    ).fetchall()
    return GridCollection(
        cell=cell,
        features=[
            CellFeature(geometry=json.loads(g), properties=CellProperties(detections=n, frp_sum=fs, static_detections=st))
            for g, n, fs, st in rows
        ],
    )


ResParam = Annotated[
    int, Query(ge=0, le=db.H3_RES, description="H3 resolution: 6 is the base cell, 0-5 roll up to its parents")
]


def _hex_collection(
    cur: duckdb.DuckDBPyConnection, base: str, filters: Filters, res: int,
    extra: tuple[str, list] | None = None, divide: int = 1,
) -> HexCollection:
    """Rows of `base` (h3_r6, detections, frp_sum, static_detections) summed per hexagon at `res`, as GeoJSON.
    `extra` adds a WHERE clause (and its parameters); `divide` turns the sums into a mean (typical: per year)."""
    where, params = filters.where()
    if extra:
        where = f"{where} and ({extra[0]})"
        params = [*params, *extra[1]]
    rows = cur.execute(
        f"""
        with cells as (
            select h3_cell_to_parent(h3_r6, ?) as cell,
                   sum(detections) / {divide} as detections, sum(frp_sum) / {divide} as frp_sum,
                   round(sum(static_detections) / {divide})::bigint as static_detections
            from {base}
            where {where}
            group by 1
        ),
        shapes as (
            select cell, detections, frp_sum, static_detections,
                   ST_GeomFromText(h3_cell_to_boundary_wkt(cell)) as geom
            from cells
        )
        select h3_h3_to_string(cell), ST_AsGeoJSON(geom), detections, frp_sum, static_detections
        from shapes
        where ST_XMax(geom) - ST_XMin(geom) < 180
        order by detections desc
        """,
        [res, *params],
    ).fetchall()
    return HexCollection(
        resolution=res,
        features=[
            HexFeature(
                geometry=json.loads(g),
                properties=HexProperties(h3=h, detections=n, frp_sum=fs, static_detections=st),
            )
            for h, g, n, fs, st in rows
        ],
    )


@router.get("/datasets/{dataset}/stats/hex")
def hex_stats(dataset: DatasetId, cur: Cursor, filters: FiltersDep, res: ResParam = 3) -> HexCollection:
    """Detections aggregated on H3 hexagons, as GeoJSON polygons.

    Every detection belongs to one fixed resolution-6 cell (h3_r6); coarser
    resolutions group those cells by their parent, so all levels share the same grid.
    bbox/zone select hexagons by their center.

    Cells crossing the antimeridian are dropped: as plain lon/lat polygons they
    would be drawn spanning the whole map.
    """
    return _hex_collection(cur, _cells(cur, dataset, filters), filters, res)


@router.get("/stats/harmonized/hex")
def harmonized_hex_stats(cur: Cursor, filters: FiltersDep, res: ResParam = 3) -> HexCollection:
    """Hexagons of the harmonized series: ONE series across the MODIS -> VIIRS change, in MODIS units (MODIS before
    2012-02, VIIRS S-NPP / k after), so a period that spans the change (say, the year 2012) comes in one request.
    Detections are fractional. Same shape as /datasets/{dataset}/stats/hex."""
    if not db.harmonized_series_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    base = db.harmonized_series_relation(with_center=filters.bbox is not None or filters.zone is not None)
    return _hex_collection(cur, base, filters, res)


# ------------------------------------------------------------ typical (anomalies) ---
# What a hexagon usually has in the asked period: the same dates in the other years, averaged. The map divides the
# period's value by it (anomalies). Only years whose shifted period lies whole inside the data, and never overlapping
# the period itself.


class TypicalHexCollection(HexCollection):
    years: int  # how many other years the mean is over (0: nothing to compare with, no features)


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February in a year without it
        return d.replace(year=d.year + years, day=28)


def _typical_windows(cur: duckdb.DuckDBPyConnection, plain: str, start: date, end: date) -> list[tuple[date, date]]:
    """The asked period moved to every other year of `plain` (rows without centers) that holds it whole."""
    first, last = cur.execute(f"select min(acq_date), max(acq_date) from {plain}").fetchone()
    if first is None:
        return []
    windows = []
    for k in range(first.year - end.year, last.year - start.year + 1):
        ws, we = _shift_years(start, k), _shift_years(end, k)
        if k and ws >= first and we <= last and (we < start or ws > end):
            windows.append((ws, we))
    return windows


def _typical(cur: duckdb.DuckDBPyConnection, plain: str, base: str, filters: Filters, res: int) -> TypicalHexCollection:
    if not (filters.start and filters.end):
        raise HTTPException(422, "start and end are required")
    windows = _typical_windows(cur, plain, filters.start, filters.end)
    if not windows:
        return TypicalHexCollection(resolution=res, features=[], years=0)
    # The other years' dates instead of the asked ones; bbox / zone / daynight stay.
    spatial = Filters(bbox=filters.bbox, zone=filters.zone, daynight=filters.daynight)
    extra = (" or ".join(["acq_date between ? and ?"] * len(windows)), [d for w in windows for d in w])
    fc = _hex_collection(cur, base, spatial, res, extra=extra, divide=len(windows))
    return TypicalHexCollection(resolution=res, features=fc.features, years=len(windows))


@router.get("/datasets/{dataset}/stats/hex/typical")
def hex_typical(dataset: DatasetId, cur: Cursor, filters: FiltersDep, res: ResParam = 3) -> TypicalHexCollection:
    """Typical hexagons of the period (start and end required): the same dates in each other year of the dataset,
    averaged (detections and FRP per year). Same shape as /stats/hex, plus `years`."""
    ds = _dataset(cur, dataset)
    plain = _relation(db.cells_relation, cur, ds, with_center=False)
    return _typical(cur, plain, _cells(cur, dataset, filters), filters, res)


@router.get("/stats/harmonized/hex/typical")
def harmonized_hex_typical(cur: Cursor, filters: FiltersDep, res: ResParam = 3) -> TypicalHexCollection:
    """Typical hexagons of the period on the harmonized series (MODIS units): same as /datasets/{dataset}/stats/hex/typical."""
    if not db.harmonized_series_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    plain = db.harmonized_series_relation(with_center=False)
    base = db.harmonized_series_relation(with_center=filters.bbox is not None or filters.zone is not None)
    return _typical(cur, plain, base, filters, res)


@router.get("/datasets/{dataset}/stats/monthly")
def monthly_stats(dataset: DatasetId, cur: Cursor, filters: FiltersDep) -> list[MonthlyStat]:
    """Detections and FRP per year and month (burn calendar)."""
    base = _cells(cur, dataset, filters)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select year(acq_date), month(acq_date), sum(detections), sum(frp_sum), sum(static_detections)
        from {base}
        where {where}
        group by all
        order by 1, 2
        """,
        params,
    ).fetchall()
    return [
        MonthlyStat(year=y, month=m, detections=n, frp_sum=fs, static_detections=st) for y, m, n, fs, st in rows
    ]


@router.get("/stats/harmonized/monthly")
def harmonized_monthly_stats(cur: Cursor, filters: FiltersDep) -> list[MonthlyStat]:
    """Burn calendar of the harmonized series: ONE continuous series across the MODIS -> VIIRS change, in MODIS units
    (MODIS with merged bow-tie duplicates before 2012-02, VIIRS S-NPP divided by the k of each hexagon after).
    Detections are fractional; FRP in MW."""
    if not db.harmonized_series_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    base = db.harmonized_series_relation(with_center=filters.bbox is not None or filters.zone is not None)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select year(acq_date), month(acq_date), sum(detections), sum(frp_sum), sum(static_detections)
        from {base}
        where {where}
        group by all
        order by 1, 2
        """,
        params,
    ).fetchall()
    return [
        MonthlyStat(year=y, month=m, detections=n, frp_sum=fs, static_detections=st) for y, m, n, fs, st in rows
    ]


# ------------------------------------------------------------- one hexagon ---
# The hexagon is the canonical entity: these endpoints hand over its products
# (identity, profile, monthly series, period summary, detections), precomputed
# in gold and looked up by key.


class HexagonParents(BaseModel):
    r5: str
    r4: str
    r3: str


class HexagonSensorProfile(BaseModel):
    """Whole-history profile of one sensor in the hexagon (continuous SP + NRT series)."""

    sensor: Sensor
    first_date: date | None
    last_date: date | None
    active_days: int
    fire_detections: float  # fractional when harmonized
    fire_frp_sum: float
    fire_frp_max: float | None
    fire_frp_mean: float | None
    fire_frp_p90: float | None
    high_conf_share: float | None
    day_share: float | None
    peak_date: date | None
    peak_day_detections: float | None
    peak_month: int | None  # 1-12
    climatology: list[float]  # detections per month of the year, Jan..Dec
    last_7d: float
    last_30d: float
    data_end_date: date | None  # last_7d / last_30d are relative to this day
    static_detections: int
    static_share: float | None
    satellites: int


class HexagonInfo(BaseModel):
    h3: str
    resolution: int
    center: tuple[float, float]  # lon, lat
    area_km2: float
    boundary: PolygonGeometry
    parents: HexagonParents
    first_date: date | None
    last_date: date | None
    has_static_source: bool
    profiles: list[HexagonSensorProfile]


class HexagonMonth(BaseModel):
    month: date
    sensor: Sensor
    satellite: str
    products: str  # sp, nrt or "nrt,sp"
    fire_detections: int
    fire_day: int
    fire_night: int
    fire_frp_sum: float | None
    fire_frp_max: float | None
    fire_high_conf: int
    active_days: int
    static_detections: int


class HexagonHarmonizedMonth(BaseModel):
    """One month of the series spliced across the MODIS -> VIIRS change, in MODIS units."""

    month: date = Field(validation_alias="acq_month")
    harmonized_source: Literal["modis", "viirs"]  # MODIS before 2012-02, VIIRS S-NPP / k from then on
    detections_harmonized: float
    frp_harmonized: float  # MW, summed over the month
    m_detections: int | None  # MODIS side, raw (merged duplicates, size-corrected FRP)
    m_frp_sum: float | None
    m_satellites: int | None  # 1 = Terra only (before 2002-07)
    v_detections: int | None  # VIIRS S-NPP side, raw
    v_frp_sum: float | None
    k_count: float  # VIIRS / MODIS factor used for this cell
    k_frp: float
    k_res: int  # H3 resolution the k comes from: 6 = the cell's own, 0 = global


class HexagonReliabilityMonth(BaseModel):
    """Persistence and reliability of the wildfire detections of one hexagon in one month, per sensor."""

    month: date = Field(validation_alias="acq_month")
    sensor: Sensor
    detections: int
    persistent_detections: int  # another pass of the same sensor within 1.5 km, 1-36 h away
    persistent_share: float
    reliability_mean: float  # mean probability that an independent sensor confirms the detection (0-1)
    reliability_sum: float  # expected number of confirmed detections (to average over several months)


class HexagonHarmonized(BaseModel):
    months: list[HexagonHarmonizedMonth]
    # Months whose data was fully downloaded for the sensor that defines the series. A month without a row in
    # `months` is a zero only if it is listed here; otherwise there is no data for it.
    covered_months: list[date]


class HexagonDay(BaseModel):
    date: date
    detections: float
    static_detections: int


class HexagonPeriodSensor(BaseModel):
    sensor: Sensor
    fire_detections: float
    active_days: int
    fire_frp_sum: float
    fire_frp_max: float | None
    high_conf_share: float | None
    day_share: float | None
    static_detections: int
    satellites: dict[str, int]  # wildfire detections per satellite
    daily: list[HexagonDay]


class HexagonPeriod(BaseModel):
    h3: str
    start: date | None
    end: date | None
    sensors: list[HexagonPeriodSensor]


class HexagonDetection(BaseModel):
    acq_datetime_utc: datetime
    solar_hour_local: float | None
    sensor: Sensor
    satellite: str | None
    product: Product
    instrument: str | None
    version: str | None
    latitude: float
    longitude: float
    frp: float | None
    confidence_raw: str | None
    confidence_level: str | None
    daynight: str | None
    brightness: float | None
    brightness_aux: float | None
    scan: float | None
    track: float | None
    fire_type: int | None
    is_static_source: bool
    is_preferred: bool


class HexagonDetections(BaseModel):
    h3: str
    dataset: DatasetId
    truncated: bool
    rows: list[HexagonDetection]


HexId = Annotated[str, Path(description="H3 resolution-6 cell id, e.g. 86c2e3117ffffff")]


def _hexagon(cur: duckdb.DuckDBPyConnection, h3_id: str) -> tuple[int, tuple]:
    """(key, dim_hexagon row) of a hexagon with data, or 422 (invalid id) / 404 (no data)."""
    try:
        key = db.hexagon_key(cur, h3_id)
    except db.InvalidHexagon as e:
        raise HTTPException(422, str(e)) from None
    if not db.hexagon_tables_ready(cur):
        raise HTTPException(404, "no hexagon data in the lake yet")
    identity = db.hexagon_identity(cur, key)
    if identity is None:
        raise HTTPException(404, f"no detections in hexagon {h3_id}")
    return key, identity


@router.get("/hexagons/{h3_id}")
def hexagon(
    h3_id: HexId,
    cur: Cursor,
    harmonized: Annotated[bool, Query(description="Profile in MODIS units (MODIS merged, VIIRS S-NPP / k)")] = False,
) -> HexagonInfo:
    """Identity and whole-history profile of a hexagon, one profile per sensor."""
    if harmonized and not db.harmonized_series_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    key, (h3, lon, lat, area, boundary, r5, r4, r3, first, last, has_static) = _hexagon(cur, h3_id)
    return HexagonInfo(
        h3=h3, resolution=db.H3_RES, center=(lon, lat), area_km2=area, boundary=json.loads(boundary),
        parents=HexagonParents(r5=r5, r4=r4, r3=r3), first_date=first, last_date=last,
        has_static_source=has_static,
        profiles=[
            HexagonSensorProfile(**p)
            for p in (db.hexagon_profiles_harmonized(cur, key) if harmonized else db.hexagon_profiles(cur, key))
        ],
    )


@router.get("/hexagons/{h3_id}/monthly")
def hexagon_monthly(h3_id: HexId, cur: Cursor) -> list[HexagonMonth]:
    """Monthly series of the hexagon (continuous series), per sensor and satellite."""
    key, _ = _hexagon(cur, h3_id)
    return [HexagonMonth(month=r.pop("acq_month"), **r) for r in db.hexagon_monthly(cur, key)]


@router.get("/hexagons/{h3_id}/harmonized")
def hexagon_harmonized(h3_id: HexId, cur: Cursor) -> HexagonHarmonized:
    """Monthly series of the hexagon as ONE continuous series across the MODIS -> VIIRS change (MODIS units).

    Before 2012-02 it is MODIS (bow-tie duplicates merged, size-corrected FRP); from then on VIIRS S-NPP divided
    by the cell's k. A month with no row had no detection ONLY if it is in `covered_months`; otherwise the data was
    never downloaded for it (the client should leave a gap, not a zero).
    """
    key, _ = _hexagon(cur, h3_id)
    if not db.harmonized_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    return HexagonHarmonized(
        months=[HexagonHarmonizedMonth.model_validate(r) for r in db.hexagon_harmonized(cur, key)],
        covered_months=db.hexagon_covered_months(cur, key),
    )


@router.get("/hexagons/{h3_id}/reliability")
def hexagon_reliability(h3_id: HexId, cur: Cursor) -> list[HexagonReliabilityMonth]:
    """Persistence and reliability index of the hexagon's detections, per month and sensor.

    The index is the probability that an independent sensor confirms a detection (logistic on confidence, persistence
    and FRP; out-of-fold AUC 0.66 MODIS / 0.73 VIIRS): a measure of how likely it is to be seen again, not of how
    likely it is to be a real fire. To average over several months weight by `detections`.
    """
    key, _ = _hexagon(cur, h3_id)
    if not db.reliability_ready(cur):
        raise HTTPException(404, "the reliability index has not been built yet")
    return [HexagonReliabilityMonth.model_validate(r) for r in db.hexagon_reliability(cur, key)]


@router.get("/hexagons/{h3_id}/period")
def hexagon_period(
    h3_id: HexId,
    cur: Cursor,
    start: Annotated[date | None, Query(description="First acquisition date (inclusive)")] = None,
    end: Annotated[date | None, Query(description="Last acquisition date (inclusive)")] = None,
    harmonized: Annotated[bool, Query(description="In MODIS units (MODIS merged, VIIRS S-NPP / k)")] = False,
) -> HexagonPeriod:
    """Profile of the hexagon for a date range, per sensor (continuous series).

    Sums the precomputed daily rows of this one hexagon (tens to hundreds of rows).
    """
    if start and end and end < start:
        raise HTTPException(422, "end must be on or after start")
    if harmonized and not db.harmonized_series_ready(cur):
        raise HTTPException(404, "the harmonized series has not been built yet")
    key, _ = _hexagon(cur, h3_id)
    by_sensor: dict[str, dict] = {}
    for sensor, satellite, day, daynight, fire, frp_sum, frp_max, high, static in db.hexagon_period(
        cur, key, start, end, harmonized
    ):
        s = by_sensor.setdefault(sensor, {"fire": 0, "frp": 0.0, "frp_max": None, "high": 0, "day": 0,
                                          "static": 0, "sats": {}, "daily": {}})
        s["fire"] += fire
        s["frp"] += frp_sum or 0.0
        if frp_max is not None:
            s["frp_max"] = max(s["frp_max"] or 0.0, frp_max)
        s["high"] += high
        s["day"] += fire if daynight == "D" else 0
        s["static"] += static
        s["sats"][satellite] = s["sats"].get(satellite, 0) + fire
        d = s["daily"].setdefault(day, [0, 0])
        d[0] += fire
        d[1] += static
    sensors = [
        HexagonPeriodSensor(
            sensor=sensor,
            fire_detections=s["fire"],
            active_days=sum(1 for f, _ in s["daily"].values() if f > 0),
            fire_frp_sum=s["frp"],
            fire_frp_max=s["frp_max"],
            high_conf_share=s["high"] / s["fire"] if s["fire"] and not harmonized else None,  # no confidence there
            day_share=s["day"] / s["fire"] if s["fire"] else None,
            static_detections=s["static"],
            satellites={} if harmonized else s["sats"],
            daily=[HexagonDay(date=d, detections=f, static_detections=st) for d, (f, st) in sorted(s["daily"].items())],
        )
        for sensor, s in sorted(by_sensor.items())
    ]
    return HexagonPeriod(h3=h3_id, start=start, end=end, sensors=sensors)


@router.get("/hexagons/{h3_id}/detections")
def hexagon_detections(
    h3_id: HexId,
    cur: Cursor,
    dataset: Annotated[DatasetId, Query(description="Which series (e.g. viirs_best)")] = DatasetId.viirs_best,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20_000)] = 2_000,
) -> HexagonDetections:
    """Every detection in the hexagon for a dataset and date range, most recent first (a table)."""
    ds = DATASETS[dataset]
    if not ds.on_hexagon_model:
        raise HTTPException(422, f"{dataset} has no per-hexagon detail")
    if start and end and end < start:
        raise HTTPException(422, "end must be on or after start")
    key, _ = _hexagon(cur, h3_id)
    rows = db.hexagon_detections(cur, key, ds, start, end, limit)
    return HexagonDetections(
        h3=h3_id, dataset=dataset, truncated=len(rows) > limit,
        rows=[HexagonDetection(**r) for r in rows[:limit]],
    )


# ----------------------------------------------------- weather of a hexagon ---
# Asked to NASA POWER on demand for the hexagon's center (app/power.py, cached per
# POWER grid cell and day): any place on Earth, with or without fires or ingestion.

WEATHER_DEFAULT_DAYS = 90
WEATHER_MAX_DAYS = 731
# Days of history before `start` fetched for the accumulations (30-day rain, days since rain).
WEATHER_LOOKBACK_DAYS = 60


class WeatherCell(BaseModel):
    """The NASA POWER grid cell (MERRA-2 grid, 0.5° x 0.625°) whose values stand for the hexagon."""

    product: Literal["power"]
    center: tuple[float, float]  # lon, lat
    distance_km: float  # from the hexagon's center
    first_date: date | None  # days with data: what a range can ask for
    last_date: date | None


class HexagonWeatherDay(BaseModel):
    """Weather of one UTC day (NASA POWER daily). Null when POWER has no value yet."""

    date: date
    t2m_max_c: float | None = None
    t2m_min_c: float | None = None
    t2m_mean_c: float | None = None
    dewpoint_mean_c: float | None = None
    rh_min_pct: float | None = None  # at the hottest moment: from max temperature and dew point
    rh_mean_pct: float | None = None
    vpd_max_kpa: float | None = None  # vapour pressure deficit: how hard the air dries fuel
    vpd_mean_kpa: float | None = None
    wind_speed_max_ms: float | None = None  # 10 m, not gusts
    wind_speed_mean_ms: float | None = None
    wind_dir_mean_deg: float | None = None  # where the wind comes from, 0 = north
    precipitation_mm: float | None = None
    precipitation_7d_mm: float | None = None  # last 7 days, this one included
    precipitation_30d_mm: float | None = None
    days_since_rain: int | None = None  # since the last day with >= rain_day_mm (null: none in the lookback)


class HexagonWeather(BaseModel):
    h3: str
    center: tuple[float, float]  # lon, lat
    start: date | None
    end: date | None
    rain_day_mm: float
    cells: list[WeatherCell]
    days: list[HexagonWeatherDay]


def _svp_kpa(t: float) -> float:
    """Saturation vapour pressure (kPa) at t °C (Magnus, Alduchov & Eskridge 1996)."""
    return 0.61094 * math.exp(17.625 * t / (t + 243.04))


def _weather_day(day: date, v: dict, series: dict[date, dict]) -> HexagonWeatherDay:
    """One day's values plus what derives from them: VPD, minimum RH, rain accumulations."""
    tmax, tmean, td = v.get("t2m_max_c"), v.get("t2m_mean_c"), v.get("dewpoint_mean_c")
    extra = {}
    if td is not None and tmax is not None:
        extra["vpd_max_kpa"] = max(0.0, _svp_kpa(tmax) - _svp_kpa(td))
        extra["rh_min_pct"] = min(100.0, 100 * _svp_kpa(td) / _svp_kpa(tmax))
    if td is not None and tmean is not None:
        extra["vpd_mean_kpa"] = max(0.0, _svp_kpa(tmean) - _svp_kpa(td))

    def rain_over(n: int) -> float | None:
        values = [series.get(day - timedelta(days=i), {}).get("precipitation_mm") for i in range(n)]
        known = [x for x in values if x is not None]
        return sum(known) if known else None

    since = next((i for i in range(WEATHER_LOOKBACK_DAYS)
                  if (series.get(day - timedelta(days=i), {}).get("precipitation_mm") or 0) >= db.RAIN_DAY_MM), None)
    return HexagonWeatherDay(date=day, **v, **extra, precipitation_7d_mm=rain_over(7),
                             precipitation_30d_mm=rain_over(30), days_since_rain=since)


@router.get("/hexagons/{h3_id}/weather")
def hexagon_weather(
    h3_id: HexId,
    cur: Cursor,
    start: Annotated[date | None, Query(description="First UTC day (default: 90 days before end)")] = None,
    end: Annotated[date | None, Query(description="Last UTC day (default: yesterday)")] = None,
) -> HexagonWeather:
    """Daily weather of the hexagon from NASA POWER (MERRA-2 / GEOS-IT, ~2 days behind):
    temperature, dew point, humidity, VPD, wind, and precipitation with its 7 / 30-day
    totals and days since rain. Any hexagon on Earth, with or without detections.

    Asked on demand and cached per POWER grid cell and day (the first view of a place
    takes ~1-2 s). 503 if POWER doesn't answer (rate limit, outage).
    """
    if start and end and end < start:
        raise HTTPException(422, "end must be on or after start")
    try:
        key = db.hexagon_key(cur, h3_id)
    except db.InvalidHexagon as e:
        raise HTTPException(422, str(e)) from None
    lat, lon = db.hexagon_center(cur, key)
    yesterday = date.today() - timedelta(days=1)
    end = min(end or yesterday, yesterday)
    start = start or end - timedelta(days=WEATHER_DEFAULT_DAYS - 1)
    if end < start:
        raise HTTPException(422, "no weather for future dates")
    if (end - start).days + 1 > WEATHER_MAX_DAYS:
        raise HTTPException(422, f"at most {WEATHER_MAX_DAYS} days per request")
    try:
        series = power.daily(lat, lon, start - timedelta(days=WEATHER_LOOKBACK_DAYS), end)
    except power.PowerUnavailable as e:
        raise HTTPException(503, f"{e}; try again in a minute") from None

    cell_lat, cell_lon = power.cell_of(lat, lon)
    known = [d for d, v in series.items() if any(x is not None for x in v.values())]
    cell = WeatherCell(
        product="power", center=(cell_lon, cell_lat),
        distance_km=round(111.2 * math.hypot(cell_lat - lat, (cell_lon - lon) * math.cos(math.radians(lat))), 1),
        first_date=power.FIRST_DAY, last_date=max(known, default=None),
    )
    days = [_weather_day(d, v, series) for d, v in series.items() if start <= d <= end]
    return HexagonWeather(h3=h3_id, center=(lon, lat), start=start, end=end, rain_day_mm=db.RAIN_DAY_MM,
                          cells=[cell], days=days)


# ------------------------------------------------- a selection of hexagons ---
# A drawn area or a multi-selection: many res-6 hexagons in one request (POST, since
# a list of thousands of ids doesn't fit in a URL). Empty hexagons are allowed.

SelectionIds = Annotated[list[str], Field(min_length=1, max_length=db.MAX_SELECTION, description="H3 res-6 ids")]


class SelectionSummaryQuery(BaseModel):
    h3: SelectionIds
    start: date | None = None
    end: date | None = None


class HexagonSummary(BaseModel):
    """One hexagon of the selection: whole history and the chosen dates, per sensor."""

    h3: str
    modis_total: int
    viirs_total: int
    last_date: date | None
    modis_period: int
    viirs_period: int
    static_period: int


class SelectionDetectionsQuery(BaseModel):
    h3: SelectionIds
    dataset: DatasetId = DatasetId.viirs_best
    start: date | None = None
    end: date | None = None
    limit: int = Field(5_000, ge=1, le=20_000)


class SelectionDetection(HexagonDetection):
    h3: str


class SelectionDetections(BaseModel):
    dataset: DatasetId
    truncated: bool
    rows: list[SelectionDetection]


def _selection_keys(cur: duckdb.DuckDBPyConnection, ids: list[str], start: date | None, end: date | None) -> list[int]:
    if start and end and end < start:
        raise HTTPException(422, "end must be on or after start")
    try:
        keys = db.hexagon_keys(cur, ids)
    except db.InvalidHexagon as e:
        raise HTTPException(422, str(e)) from None
    if not db.hexagon_tables_ready(cur):
        raise HTTPException(404, "no hexagon data in the lake yet")
    return keys


class SelectionWeatherQuery(BaseModel):
    h3: SelectionIds
    start: date
    end: date
    # Rings of neighbors around the selected hexagons that get weather too (the map fades
    # them with the distance); ignored for big selections (a drawn area covers itself).
    rings: int = Field(3, ge=0, le=3)


class SelectionWeather(BaseModel):
    """The map's weather values for the POWER cells under a selection, every day of the range."""

    start: date
    end: date
    cell: tuple[float, float]  # POWER cell size (lat, lon), degrees
    cells: list[tuple[float, float]]  # [lon, lat] of each cell center
    hexagons: dict[str, int]  # selected h3 -> index in `cells`
    sources: dict[str, int]  # cells served by the lake (already ingested) and by NASA POWER (asked now)
    # Per day: [t2m_max_c, precipitation_mm, wind_speed_mean_ms, wind_dir_mean_deg] per cell (index order).
    days: list[tuple[date, list[list[float | None]]]]


SELECTION_WEATHER_MAX_DAYS = 366
SELECTION_RINGS_MAX_HEXAGONS = 300  # bigger selections (drawn areas) get no extra rings


@router.post("/hexagons/selection/weather")
def selection_weather(body: SelectionWeatherQuery, cur: Cursor) -> SelectionWeather:
    """Daily weather (NASA POWER, on demand) of the selected hexagons, for the map: max
    temperature, rain and mean wind of the POWER cell under each hexagon, every day of
    the range, so the time slider animates it without more requests.

    The selected hexagons and up to `rings` rings of neighbors, grouped by POWER cell
    (0.5° x 0.625°: ~90 hexagons each). Cells the lake already has for the whole range
    (ingested near fires) come from it; only the rest is asked to POWER: a few cells as
    points (which also warms the panel's cache), more by region, one request per
    parameter and 10° box. Cached; 503 if POWER doesn't answer.
    """
    yesterday = date.today() - timedelta(days=1)
    end = min(body.end, yesterday)
    if end < body.start:
        raise HTTPException(422, "end must be on or after start (and not in the future)")
    if (end - body.start).days + 1 > SELECTION_WEATHER_MAX_DAYS:
        raise HTTPException(422, f"at most {SELECTION_WEATHER_MAX_DAYS} days per request")
    ids = list(dict.fromkeys(body.h3))
    try:
        keys = db.hexagon_keys(cur, ids)
    except db.InvalidHexagon as e:
        raise HTTPException(422, str(e)) from None
    centers = dict(zip(ids, db.hexagon_centers(cur, keys)))
    rings = body.rings if len(keys) <= SELECTION_RINGS_MAX_HEXAGONS else 0
    points = db.hexagon_neighborhood(cur, keys, rings) if rings else list(centers.values())
    wanted = sorted({power.cell_of(lat, lon) for lat, lon in points})

    # The lake first (weather already ingested near fires); NASA POWER only for what it
    # lacks: cells away from fires, and the days the lake doesn't have yet (ingestion
    # runs daily, the range usually ends today), from the first missing day on.
    all_days = [body.start + timedelta(days=i) for i in range((end - body.start).days + 1)]
    lake = db.weather_cells_daily(cur, wanted, body.start, end)
    values = {c: dict(days) for c, days in lake.items()}
    first_missing = {}
    for c in wanted:
        have = values.get(c, {})
        gap = next((d for d in all_days if have.get(d, {}).get("t2m_max_c") is None), None)
        if gap is not None:
            first_missing[c] = gap
    missing = sorted(first_missing)
    if missing:
        try:
            asked = power.map_values(missing, min(first_missing.values()), end)
        except power.PowerUnavailable as e:
            raise HTTPException(503, f"{e}; try again in a minute") from None
        for c, days in asked.items():
            for d, v in days.items():
                if values.get(c, {}).get(d, {}).get("t2m_max_c") is None:  # the lake wins where it has a value
                    values.setdefault(c, {})[d] = v
    cells = sorted(values)
    index = {c: i for i, c in enumerate(cells)}
    names = list(power.MAP_PARAMETERS.values())
    all_days = sorted({d for days in values.values() for d in days})
    return SelectionWeather(
        start=body.start, end=end, cell=power.GRID,
        cells=[(lon, lat) for lat, lon in cells],
        hexagons={h: index[power.cell_of(*c)] for h, c in centers.items()},
        sources={"lake": len(wanted) - len(missing), "power": len(missing)},  # cells complete from the lake / topped up by POWER
        days=[(d, [[values[c].get(d, {}).get(n) for n in names] for c in cells]) for d in all_days],
    )


@router.post("/hexagons/selection/summary")
def selection_summary(body: SelectionSummaryQuery, cur: Cursor) -> list[HexagonSummary]:
    """The hexagons of a selection that have any history, busiest in the chosen dates first.

    Hexagons never seen burning are left out (the caller knows its own list).
    """
    keys = _selection_keys(cur, body.h3, body.start, body.end)
    fields = HexagonSummary.model_fields
    return [HexagonSummary(**dict(zip(fields, row))) for row in db.selection_summary(cur, keys, body.start, body.end)]


@router.post("/hexagons/selection/detections")
def selection_detections(body: SelectionDetectionsQuery, cur: Cursor) -> SelectionDetections:
    """Every detection of the selected hexagons for a dataset and dates, most recent first."""
    ds = DATASETS[body.dataset]
    if not ds.on_hexagon_model:
        raise HTTPException(422, f"{body.dataset} has no per-hexagon detail")
    keys = _selection_keys(cur, body.h3, body.start, body.end)
    rows = db.selection_detections(cur, keys, ds, body.start, body.end, body.limit)
    return SelectionDetections(
        dataset=body.dataset, truncated=len(rows) > body.limit,
        rows=[SelectionDetection(**r) for r in rows[: body.limit]],
    )


# --------------------------------------------------------------- weather maps ---
# One day of one weather variable over the map's area: cell centers and values, as
# compact arrays (thousands of cells; GeoJSON would be ten times bigger).

WeatherLayerId = Literal["temperature", "precipitation", "wind"]
WEATHER_UNITS = {"temperature": "°C", "precipitation": "mm", "wind": "m/s"}


class WeatherCoverage(BaseModel):
    first_date: date
    last_date: date


class WeatherMap(BaseModel):
    layer: WeatherLayerId
    product: Literal["power"]
    date: date
    unit: str
    cell: tuple[float, float]  # size of each drawn cell, degrees (lat, lon); coarser than native when zoomed out
    # [lon, lat, value] per cell center; wind adds the direction it comes from: [lon, lat, speed, deg].
    cells: list[list[float]]
    coverage: WeatherCoverage | None  # days the layer has anywhere: to say what to pick when `cells` is empty


@router.get("/weather/map")
def weather_map(
    cur: Cursor,
    layer: Annotated[WeatherLayerId, Query(description="temperature (daily max) | precipitation (daily total) | wind (daily mean)")],
    day: Annotated[date, Query(alias="date", description="UTC day")],
    bbox: Annotated[str | None, Query(description="minLon,minLat,maxLon,maxLat (default: the whole loaded area)")] = None,
) -> WeatherMap:
    """A weather layer for the map, near fires (NASA POWER daily, ingested by the ETL):
    one UTC day of max temperature, precipitation (dry cells under 0.1 mm left out) or
    mean wind (speed and the direction it comes from) inside bbox."""
    spec = db.WEATHER_LAYERS[layer]
    if not db.weather_ready(cur):
        raise HTTPException(404, "no weather data in the lake yet")
    extent = db.weather_extent(cur, spec, day)
    area = _parse_bbox(bbox) if bbox else extent
    rows: list[tuple] = []
    cell = spec.cell
    if extent and area:
        # Only the part of the area with data counts for the cell size.
        clipped = db.BBox(max(area.min_lon, extent.min_lon), max(area.min_lat, extent.min_lat),
                          min(area.max_lon, extent.max_lon), min(area.max_lat, extent.max_lat))
        if clipped.min_lon <= clipped.max_lon and clipped.min_lat <= clipped.max_lat:
            cell = db.weather_map_cell(spec, clipped)
            rows = db.weather_map(cur, spec, day, area, cell)
    coverage = None
    if not rows:
        found = db.weather_layer_coverage(cur, spec)
        coverage = WeatherCoverage(first_date=found[0], last_date=found[1]) if found else None
    return WeatherMap(
        layer=layer, product="power", date=day, unit=WEATHER_UNITS[layer], cell=cell, coverage=coverage,
        cells=[[round(v, 4) if i < 2 else round(v, 2) for i, v in enumerate(r)] for r in rows if r[2] is not None],
    )
