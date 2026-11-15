// The full H3 grid of an area, generated in the browser: every cell, with or without
// detections, so the map always shows the same fixed grid. Same library and ids as
// the backend's DuckDB H3 extension, so these cells line up exactly with the data.
import { cellToBoundary, cellsToMultiPolygon, getHexagonEdgeLengthAvg, polygonToCells } from 'h3-js'

const MAX_CELLS = 60_000 // safety net: never build a huge GeoJSON
const CHUNK_DEG = 60 // H3 polygons must be narrower than 180° of longitude
const DENSIFY_DEG = 2 // H3 polygon edges are great circles: add vertices so they follow the parallels

// Area covered by the last grid built (bigger than the viewport), to reuse it while panning.
export function gridArea(bounds, res, margin = 0.5) {
  const w = bounds.getWest(), e = bounds.getEast(), s = bounds.getSouth(), n = bounds.getNorth()
  const dx = (e - w) * margin, dy = (n - s) * margin
  return {
    res,
    west: Math.max(w - dx, -180), east: Math.min(e + dx, 180),
    south: Math.max(s - dy, -85), north: Math.min(n + dy, 85),
  }
}

export const covers = (area, bounds, res) =>
  area && area.res === res &&
  bounds.getWest() >= area.west - 1e-9 && bounds.getEast() <= area.east + 1e-9 &&
  bounds.getSouth() >= area.south - 1e-9 && bounds.getNorth() <= area.north + 1e-9

function ring(west, south, east, north) {
  // [lat, lng] pairs, counter-clockwise, densified along every edge.
  const pts = []
  const steps = (a, b) => Math.max(1, Math.ceil(Math.abs(b - a) / DENSIFY_DEG))
  const edge = (lat0, lng0, lat1, lng1) => {
    const n = steps(lat0, lat1) * steps(lng0, lng1)
    for (let i = 0; i < n; i++) pts.push([lat0 + ((lat1 - lat0) * i) / n, lng0 + ((lng1 - lng0) * i) / n])
  }
  edge(south, west, south, east)
  edge(south, east, north, east)
  edge(north, east, north, west)
  edge(north, west, south, west)
  return pts
}

// GeoJSON FeatureCollection of every res-`res` cell touching the area, or null when too many.
export function gridCells(area) {
  const { res } = area
  // Pad by about one cell so the cells at the border (center outside the area) are included.
  const padLat = (getHexagonEdgeLengthAvg(res, 'km') * 2) / 111
  const maxLat = Math.min(85, Math.max(Math.abs(area.south), Math.abs(area.north)) + padLat)
  const padLng = Math.min(padLat / Math.cos((maxLat * Math.PI) / 180), 10)
  const south = Math.max(area.south - padLat, -85), north = Math.min(area.north + padLat, 85)
  const west = Math.max(area.west - padLng, -180), east = Math.min(area.east + padLng, 180)

  const ids = new Set()
  for (let w = west; w < east; w += CHUNK_DEG) {
    for (const id of polygonToCells(ring(w, south, Math.min(w + CHUNK_DEG, east), north), res)) ids.add(id)
    if (ids.size > MAX_CELLS) return null
  }

  const features = []
  for (const h3 of ids) {
    const boundary = cellToBoundary(h3, true) // [lng, lat], closed ring
    const lngs = boundary.map((p) => p[0])
    if (Math.max(...lngs) - Math.min(...lngs) > 180) continue // crosses the antimeridian (as the backend)
    features.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [boundary] }, properties: { h3 } })
  }
  return { type: 'FeatureCollection', features }
}

// ----------------------------------------------------------- drawn areas ---
// An area selects every res-6 hexagon whose center falls inside it, with or without
// detections: the same rule the backend uses for its `zone` filter, so the selection
// and the filtered map always agree.

export const BASE_RES = 6
export const MAX_SELECTION = 5000 // same cap as the backend's selection endpoints
const AVG_CELL_KM2 = 36.13 // average res-6 cell

// The 4 clicked points as a simple polygon: sorted by angle around their center,
// so clicking in any order never gives a self-crossing "bow tie".
export function areaPolygon(points) {
  const cx = points.reduce((s, p) => s + p[0], 0) / points.length
  const cy = points.reduce((s, p) => s + p[1], 0) / points.length
  const ring = [...points].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx))
  return { type: 'Polygon', coordinates: [[...ring, ring[0]]] }
}

// Rough area in km² (good enough to refuse huge areas before generating cells).
function approxKm2(polygon) {
  const ring = polygon.coordinates[0]
  const lat0 = (ring.reduce((s, p) => s + p[1], 0) / ring.length) * (Math.PI / 180)
  let twice = 0
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i], [x2, y2] = ring[i + 1]
    twice += x1 * Math.cos(lat0) * y2 - x2 * Math.cos(lat0) * y1
  }
  return (Math.abs(twice) / 2) * 111.32 * 111.32
}

// Res-6 cells of a drawn polygon, or null when there would be more than MAX_SELECTION.
export function cellsInArea(polygon) {
  if (approxKm2(polygon) / AVG_CELL_KM2 > MAX_SELECTION * 1.5) return null
  const cells = polygonToCells(polygon.coordinates, BASE_RES, true)
  return cells.length > MAX_SELECTION ? null : cells
}

// Outline of a set of cells (their union), as a GeoJSON MultiPolygon.
export const cellsOutline = (cells) => ({ type: 'MultiPolygon', coordinates: cellsToMultiPolygon(cells, true) })

export const cellPolygon = (h3) => ({ type: 'Polygon', coordinates: [cellToBoundary(h3, true)] })
