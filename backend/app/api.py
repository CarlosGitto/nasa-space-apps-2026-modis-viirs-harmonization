"""HTTP endpoints. Same contract for every dataset (MODIS, VIIRS, harmonized).

`detections` counts are wildfires; heat from static sources (refineries, gas
flares, industry, volcanoes) is reported apart as `static_detections`.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Path, Query
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


class DailyStat(BaseModel):
    date: date
    detections: int
    day: int
    night: int
    frp_sum: float | None
    frp_mean: float | None
    static_detections: int


class MonthlyStat(BaseModel):
    year: int
    month: int  # 1-12
    detections: int
    frp_sum: float | None
    static_detections: int


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[tuple[float, float]]]


class CellProperties(BaseModel):
    detections: int
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
    detections: int
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


@router.get("/datasets/{dataset}/stats/daily")
def daily_stats(dataset: DatasetId, cur: Cursor, filters: FiltersDep) -> list[DailyStat]:
    """Detections and FRP per acquisition date."""
    base = _cells(cur, dataset, filters)
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
    bbox/zone select hexagons by their center.

    Cells crossing the antimeridian are dropped: as plain lon/lat polygons they
    would be drawn spanning the whole map.
    """
    base = _cells(cur, dataset, filters)
    where, params = filters.where()
    rows = cur.execute(
        f"""
        with cells as (
            select h3_cell_to_parent(h3_r6, ?) as cell,
                   sum(detections) as detections, sum(frp_sum) as frp_sum,
                   sum(static_detections) as static_detections
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
    fire_detections: int
    fire_frp_sum: float
    fire_frp_max: float | None
    fire_frp_mean: float | None
    fire_frp_p90: float | None
    high_conf_share: float | None
    day_share: float | None
    peak_date: date | None
    peak_day_detections: int | None
    peak_month: int | None  # 1-12
    climatology: list[int]  # detections per month of the year, Jan..Dec
    last_7d: int
    last_30d: int
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


class HexagonDay(BaseModel):
    date: date
    detections: int
    static_detections: int


class HexagonPeriodSensor(BaseModel):
    sensor: Sensor
    fire_detections: int
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
def hexagon(h3_id: HexId, cur: Cursor) -> HexagonInfo:
    """Identity and whole-history profile of a hexagon, one profile per sensor."""
    key, (h3, lon, lat, area, boundary, r5, r4, r3, first, last, has_static) = _hexagon(cur, h3_id)
    return HexagonInfo(
        h3=h3, resolution=db.H3_RES, center=(lon, lat), area_km2=area, boundary=json.loads(boundary),
        parents=HexagonParents(r5=r5, r4=r4, r3=r3), first_date=first, last_date=last,
        has_static_source=has_static,
        profiles=[HexagonSensorProfile(**p) for p in db.hexagon_profiles(cur, key)],
    )


@router.get("/hexagons/{h3_id}/monthly")
def hexagon_monthly(h3_id: HexId, cur: Cursor) -> list[HexagonMonth]:
    """Monthly series of the hexagon (continuous series), per sensor and satellite."""
    key, _ = _hexagon(cur, h3_id)
    return [HexagonMonth(month=r.pop("acq_month"), **r) for r in db.hexagon_monthly(cur, key)]


@router.get("/hexagons/{h3_id}/period")
def hexagon_period(
    h3_id: HexId,
    cur: Cursor,
    start: Annotated[date | None, Query(description="First acquisition date (inclusive)")] = None,
    end: Annotated[date | None, Query(description="Last acquisition date (inclusive)")] = None,
) -> HexagonPeriod:
    """Profile of the hexagon for a date range, per sensor (continuous series).

    Sums the precomputed daily rows of this one hexagon (tens to hundreds of rows).
    """
    if start and end and end < start:
        raise HTTPException(422, "end must be on or after start")
    key, _ = _hexagon(cur, h3_id)
    by_sensor: dict[str, dict] = {}
    for sensor, satellite, day, daynight, fire, frp_sum, frp_max, high, static in db.hexagon_period(cur, key, start, end):
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
            high_conf_share=s["high"] / s["fire"] if s["fire"] else None,
            day_share=s["day"] / s["fire"] if s["fire"] else None,
            static_detections=s["static"],
            satellites=s["sats"],
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


class SelectionWeather(BaseModel):
    """The map's weather values for the POWER cells under a selection, every day of the range."""

    start: date
    end: date
    cell: tuple[float, float]  # POWER cell size (lat, lon), degrees
    cells: list[tuple[float, float]]  # [lon, lat] of each cell center
    hexagons: dict[str, int]  # h3 -> index in `cells`
    # Per day: [t2m_max_c, precipitation_mm, wind_speed_mean_ms, wind_dir_mean_deg] per cell (index order).
    days: list[tuple[date, list[list[float | None]]]]


SELECTION_WEATHER_MAX_DAYS = 366


@router.post("/hexagons/selection/weather")
def selection_weather(body: SelectionWeatherQuery, cur: Cursor) -> SelectionWeather:
    """Daily weather (NASA POWER, on demand) of the selected hexagons, for the map: max
    temperature, rain and mean wind of the POWER cell under each hexagon, every day of
    the range, so the time slider animates it without more requests.

    Hexagons are grouped by POWER cell (0.5° x 0.625°: ~90 hexagons each). A few cells
    are asked as points (which also warms the panel's cache); more, by region, one
    request per parameter and 10° box. Cached; 503 if POWER doesn't answer.
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
    try:
        values = power.map_values(list(centers.values()), body.start, end)
    except power.PowerUnavailable as e:
        raise HTTPException(503, f"{e}; try again in a minute") from None
    cells = sorted(values)
    index = {c: i for i, c in enumerate(cells)}
    names = list(power.MAP_PARAMETERS.values())
    all_days = sorted({d for days in values.values() for d in days})
    return SelectionWeather(
        start=body.start, end=end, cell=power.GRID,
        cells=[(lon, lat) for lat, lon in cells],
        hexagons={h: index[power.cell_of(*c)] for h, c in centers.items()},
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
