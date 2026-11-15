"""HTTP endpoints. Same contract for every dataset (MODIS, VIIRS, harmonized)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated, Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.db import DATASETS, BBox, Dataset, DatasetId, Filters, Product, Sensor, Zone

router = APIRouter()

Cursor = Annotated[duckdb.DuckDBPyConnection, Depends(db.get_cursor)]


# ------------------------------------------------------------------ models ---


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class DatasetInfo(BaseModel):
    id: DatasetId
    sensor: Sensor
    product: Product
    available: bool
    rows: int
    start_date: date | None
    end_date: date | None


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # lon, lat


class DetectionProperties(BaseModel):
    sensor: Sensor
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


class DailyStat(BaseModel):
    date: date
    detections: int
    day: int
    night: int
    frp_sum: float | None
    frp_mean: float | None


class MonthlyStat(BaseModel):
    year: int
    month: int  # 1-12
    detections: int
    frp_sum: float | None


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[tuple[float, float]]]


class CellProperties(BaseModel):
    detections: int
    frp_sum: float | None


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
    detections: int
    frp_sum: float | None


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


def _source(cur: duckdb.DuckDBPyConnection, dataset: DatasetId) -> tuple[Dataset, str]:
    """The dataset and its base subquery, or 404 if nothing has landed yet."""
    ds = DATASETS[dataset]
    if not db.exists(cur, ds):
        raise HTTPException(404, f"no data yet for {dataset} (no table gold.{dataset} in the lake)")
    try:
        return ds, db.base_relation(cur, ds)
    except db.DatasetSchemaError as e:
        raise HTTPException(500, str(e)) from None


# --------------------------------------------------------------- endpoints ---


@router.get("/health")
def health() -> Health:
    return Health()


@router.get("/datasets")
def list_datasets(cur: Cursor) -> list[DatasetInfo]:
    """Every dataset the API knows about, with what is currently in the lake."""
    out = []
    for ds in DATASETS.values():
        rows, start, end = 0, None, None
        available = db.exists(cur, ds)
        if available:
            try:
                rows, start, end = cur.execute(
                    f"select count(*), min(acq_date), max(acq_date) from {db.base_relation(cur, ds)}"
                ).fetchone()
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
    """Fire detections as GeoJSON points, most recent first."""
    ds, base = _source(cur, dataset)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select ST_AsGeoJSON(ST_Point(longitude, latitude)) as geometry,
               satellite, acq_datetime_utc, frp, confidence, daynight, brightness
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
                sensor=ds.sensor, satellite=satellite, acq_datetime_utc=acq_datetime_utc, frp=frp,
                confidence=confidence, daynight=daynight, brightness=brightness,
            ),
        )
        for geometry, satellite, acq_datetime_utc, frp, confidence, daynight, brightness in rows[:limit]
    ]
    return DetectionCollection(features=features, truncated=len(rows) > limit)


@router.get("/datasets/{dataset}/stats/daily")
def daily_stats(dataset: DatasetId, cur: Cursor, filters: FiltersDep) -> list[DailyStat]:
    """Detections and FRP per acquisition date."""
    _, base = _source(cur, dataset)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select acq_date, count(*),
               count(*) filter (where daynight = 'D'),
               count(*) filter (where daynight = 'N'),
               sum(frp), avg(frp)
        from {base}
        where {where}
        group by acq_date
        order by acq_date
        """,
        params,
    ).fetchall()
    return [
        DailyStat(date=d, detections=n, day=day, night=night, frp_sum=fs, frp_mean=fm)
        for d, n, day, night, fs, fm in rows
    ]


@router.get("/datasets/{dataset}/stats/grid")
def grid_stats(
    dataset: DatasetId,
    cur: Cursor,
    filters: FiltersDep,
    cell: Annotated[float, Query(ge=0.1, le=10, description="Cell size in degrees")] = 1.0,
) -> GridCollection:
    """Detections aggregated on a lat/lon grid, as GeoJSON polygons (for heatmaps)."""
    _, base = _source(cur, dataset)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        with cells as (
            select floor(longitude / ?) * ? as x0, floor(latitude / ?) * ? as y0,
                   count(*) as detections, sum(frp) as frp_sum
            from {base}
            where {where}
            group by all
        )
        select ST_AsGeoJSON(ST_MakeEnvelope(x0, y0, least(x0 + ?, 180), least(y0 + ?, 90))),
               detections, frp_sum
        from cells
        order by detections desc
        """,
        [cell, cell, cell, cell, *params, cell, cell],
    ).fetchall()
    return GridCollection(
        cell=cell,
        features=[
            CellFeature(geometry=json.loads(g), properties=CellProperties(detections=n, frp_sum=fs))
            for g, n, fs in rows
        ],
    )


@router.get("/datasets/{dataset}/stats/hex")
def hex_stats(
    dataset: DatasetId,
    cur: Cursor,
    filters: FiltersDep,
    res: Annotated[
        int, Query(ge=0, le=db.H3_RES, description="H3 resolution: 6 is the base cell, 0-5 roll up to its parents")
    ] = 3,
) -> HexCollection:
    """Detections aggregated on H3 hexagons, as GeoJSON polygons.

    Every detection belongs to one fixed resolution-6 cell (h3_r6); coarser
    resolutions group those cells by their parent, so all levels share the same grid.

    Cells crossing the antimeridian are dropped: as plain lon/lat polygons they
    would be drawn spanning the whole map.
    """
    _, base = _source(cur, dataset)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        with cells as (
            select h3_cell_to_parent(h3_r6, ?) as cell,
                   count(*) as detections, sum(frp) as frp_sum
            from {base}
            where {where} and h3_r6 is not null
            group by 1
        ),
        shapes as (
            select cell, detections, frp_sum,
                   ST_GeomFromText(h3_cell_to_boundary_wkt(cell)) as geom
            from cells
        )
        select h3_h3_to_string(cell), ST_AsGeoJSON(geom), detections, frp_sum
        from shapes
        where ST_XMax(geom) - ST_XMin(geom) < 180
        order by detections desc
        """,
        [res, *params],
    ).fetchall()
    return HexCollection(
        resolution=res,
        features=[
            HexFeature(geometry=json.loads(g), properties=HexProperties(h3=h, detections=n, frp_sum=fs))
            for h, g, n, fs in rows
        ],
    )


@router.get("/datasets/{dataset}/stats/monthly")
def monthly_stats(dataset: DatasetId, cur: Cursor, filters: FiltersDep) -> list[MonthlyStat]:
    """Detections and FRP per year and month (burn calendar)."""
    _, base = _source(cur, dataset)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        select year(acq_date), month(acq_date), count(*), sum(frp)
        from {base}
        where {where}
        group by all
        order by 1, 2
        """,
        params,
    ).fetchall()
    return [MonthlyStat(year=y, month=m, detections=n, frp_sum=fs) for y, m, n, fs in rows]
