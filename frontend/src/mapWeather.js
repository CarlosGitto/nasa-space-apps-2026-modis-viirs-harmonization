// Weather layers of the main map (GET /weather/map): one UTC day of temperature,
// precipitation or wind. Fire stays the protagonist: weather uses colors the fire
// palette doesn't (no yellow/orange/red), sits below the fire hexagons and is lighter.
//
// Two scopes:
// - "fires" (default): weather only where it explains a fire. The hexagons with
//   detections on screen and their neighbors (H3 k=1 ring) take the value of the
//   weather cell under their center, as a halo under the fire; wind arrows sit on the
//   fire hexagons only. Built in the browser from the hexagons already loaded and one
//   cached grid per day, so panning and the slider don't query the lake again.
// - "all": the whole field. Temperature is a regular grid, painted into one image (a
//   canvas stretched over the map) instead of thousands of polygons; rain dots and wind
//   arrows are thinned to one per RAIN_SPACING_PX / ARROW_SPACING_PX for the zoom.
//
// Only temperature is a color fill. Rain is dots (size = mm) and wind arrows (size =
// speed), in one neutral color each, so neither competes with the fire's colors.
import { cellToBoundary, cellToLatLng, gridDisk } from 'h3-js'

// Layer id -> how it's shown. stops: [value, color] for the ramp and the legend.
export const WEATHER_LAYERS = {
  temperature: {
    label: 'Temperature',
    title: 'Max temperature (°C) · NASA MERRA-2',
    // Blue (cold) -> white -> purple (hot): stays clear of the fire's yellow-orange-red.
    stops: [[-10, '#2166ac'], [0, '#67a9cf'], [10, '#d1e5f0'], [20, '#f7f7f7'], [30, '#c2a5cf'], [40, '#762a83']],
    opacity: 0.45,
  },
  precipitation: {
    label: 'Rain',
    title: 'Rain (mm/day) · NASA GPM IMERG · dot size = mm, dry places without dot',
    dots: [1, 5, 20], // mm, for the legend
  },
  wind: {
    label: 'Wind',
    title: 'Wind (daily mean) · NASA MERRA-2 · arrows point where it blows, size = speed',
    arrows: [[2, 'light'], [8, 'moderate'], [15, 'strong']], // m/s, for the legend
  },
}
export const WEATHER_IDS = Object.keys(WEATHER_LAYERS)
export const ARROW_COLOR = '#334155'
export const RAIN_COLOR = '#2563eb'
const ARROW_SPACING_PX = 45
const RAIN_SPACING_PX = 16
const RAIN_MIN_MM = 0.1

// Rain dot radius (px) for a daily total (mm): area grows with the rain, capped at 50 mm.
export const RAIN_RADIUS = ['interpolate', ['linear'], ['sqrt', ['get', 'value']], 0, 1.5, Math.sqrt(50), 9]
export const rainRadius = (mm) => 1.5 + (Math.sqrt(Math.min(mm, 50)) / Math.sqrt(50)) * 7.5

// CSS gradient for the legend, stops spread evenly (the ramp is not linear in value).
export const legendGradient = (id) => {
  const stops = WEATHER_LAYERS[id].stops
  return `linear-gradient(to right, ${stops.map(([, c], i) => `${c} ${(i / (stops.length - 1)) * 100}%`).join(', ')})`
}

// MapLibre color expression over a feature's `value` from a layer's stops.
export const colorRamp = (id) => ['interpolate', ['linear'], ['get', 'value'], ...WEATHER_LAYERS[id].stops.flat()]

// MapLibre size expression for an arrow of a given speed (m/s), shared with the legend.
export const ARROW_SIZE = ['interpolate', ['linear'], ['get', 'value'], 0, 0.35, 15, 0.85]
export const arrowSize = (v) => 0.35 + (Math.min(v, 15) / 15) * 0.5

// ------------------------------------------------------------------ grids ---

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const RAMPS = Object.fromEntries(
  Object.entries(WEATHER_LAYERS).filter(([, l]) => l.stops).map(([id, l]) => [id, l.stops.map(([v, c]) => [v, rgb(c)])]),
)

function colorAt(ramp, v) {
  if (v <= ramp[0][0]) return ramp[0][1]
  for (let i = 1; i < ramp.length; i++) {
    const [v1, c1] = ramp[i]
    if (v <= v1) {
      const [v0, c0] = ramp[i - 1]
      const t = (v - v0) / (v1 - v0)
      return c0.map((c, k) => Math.round(c + (c1[k] - c) * t))
    }
  }
  return ramp.at(-1)[1]
}

// The response's cells as a dense grid: lookups by position and the raster below.
export function weatherGrid(response) {
  if (!response?.cells?.length) return null
  const [dlat, dlon] = response.cell
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity
  for (const [lon, lat] of response.cells) {
    if (lon < minLon) minLon = lon
    if (lon > maxLon) maxLon = lon
    if (lat < minLat) minLat = lat
    if (lat > maxLat) maxLat = lat
  }
  const cols = Math.round((maxLon - minLon) / dlon) + 1
  const rows = Math.round((maxLat - minLat) / dlat) + 1
  const index = new Int32Array(rows * cols).fill(-1) // -> position in response.cells
  response.cells.forEach(([lon, lat], i) => {
    index[Math.round((lat - minLat) / dlat) * cols + Math.round((lon - minLon) / dlon)] = i
  })
  return {
    response, rows, cols, dlat, dlon, minLon, minLat,
    west: minLon - dlon / 2, east: maxLon + dlon / 2, south: minLat - dlat / 2, north: maxLat + dlat / 2,
    // The cell under a point: [lon, lat, value(, from)], or null.
    at(lon, lat) {
      const c = Math.round((lon - minLon) / dlon)
      const r = Math.round((lat - minLat) / dlat)
      if (c < 0 || r < 0 || c >= cols || r >= rows) return null
      const i = index[r * cols + c]
      return i < 0 ? null : response.cells[i]
    },
    value(r, c) {
      const i = index[r * cols + c]
      return i < 0 ? null : response.cells[i][2]
    },
  }
}

const mercatorY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))
const latFromMercatorY = (y) => ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI

// One image for a temperature or rain grid: { url, coordinates } for a MapLibre image
// source. MapLibre stretches an image linearly in Mercator, so rows are laid out in
// Mercator Y (else cells drift north/south); one pixel per cell across, a few per row.
export function weatherRaster(id, grid) {
  if (grid.raster) return grid.raster // painted once per grid, reused while panning
  const ramp = RAMPS[id]
  const width = grid.cols
  const height = Math.min(4096, grid.rows * 3)
  const yNorth = mercatorY(Math.min(grid.north, 85))
  const ySouth = mercatorY(Math.max(grid.south, -85))
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  const img = ctx.createImageData(width, height)
  for (let y = 0; y < height; y++) {
    const lat = latFromMercatorY(yNorth - ((y + 0.5) / height) * (yNorth - ySouth))
    const r = Math.min(grid.rows - 1, Math.max(0, Math.floor((lat - grid.south) / grid.dlat)))
    for (let c = 0; c < width; c++) {
      const v = grid.value(r, c)
      if (v === null) continue // transparent: no data (or a dry cell)
      const [R, G, B] = colorAt(ramp, v)
      const p = (y * width + c) * 4
      img.data[p] = R
      img.data[p + 1] = G
      img.data[p + 2] = B
      img.data[p + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
  const north = Math.min(grid.north, 85)
  const south = Math.max(grid.south, -85)
  grid.raster = {
    url: canvas.toDataURL('image/png'),
    coordinates: [[grid.west, north], [grid.east, north], [grid.east, south], [grid.west, south]],
  }
  return grid.raster
}

// Wind arrows for a zoom: at most one per ARROW_SPACING_PX on screen (the cell nearest
// to each bin's center), as points with the direction the wind blows TO.
export function windArrows(response, zoom) {
  if (!response?.cells?.length) return { type: 'FeatureCollection', features: [] }
  const pxPerDegree = (512 * 2 ** zoom) / 360
  const step = Math.max(response.cell[1], ARROW_SPACING_PX / pxPerDegree)
  const bins = new Map()
  for (const cell of response.cells) {
    const [lon, lat] = cell
    const key = `${Math.floor(lon / step)},${Math.floor(lat / step)}`
    const center = [(Math.floor(lon / step) + 0.5) * step, (Math.floor(lat / step) + 0.5) * step]
    const d = (lon - center[0]) ** 2 + (lat - center[1]) ** 2
    const best = bins.get(key)
    if (!best || d < best.d) bins.set(key, { cell, d })
  }
  return {
    type: 'FeatureCollection',
    features: [...bins.values()].map(({ cell: [lon, lat, value, from] }) => ({
      type: 'Feature',
      properties: { value, to: (from + 180) % 360 },
      geometry: { type: 'Point', coordinates: [lon, lat] },
    })),
  }
}

// ------------------------------------------------------------- around fires ---

// The fire hexagons plus their neighbors (k=1 ring): where weather is shown.
export const fireNeighborhood = (fireCells) => new Set(fireCells.flatMap((h) => gridDisk(h, 1)))

// Each hexagon of the neighborhood colored by the weather cell under its center
// (hexagons without a value, e.g. dry for rain, are left out).
export function fireHalo(cells, grid) {
  const features = []
  for (const h of cells) {
    const [lat, lon] = cellToLatLng(h)
    const cell = grid.at(lon, lat)
    if (!cell) continue
    features.push({
      type: 'Feature',
      properties: { value: cell[2] },
      geometry: { type: 'Polygon', coordinates: [cellToBoundary(h, true)] },
    })
  }
  return { type: 'FeatureCollection', features }
}

// A rain dot at the center of each hexagon of the neighborhood where it rained.
export function fireRainDots(cells, grid) {
  const features = []
  for (const h of cells) {
    const [lat, lon] = cellToLatLng(h)
    const cell = grid.at(lon, lat)
    if (cell && cell[2] >= RAIN_MIN_MM) {
      features.push({ type: 'Feature', properties: { value: cell[2] }, geometry: { type: 'Point', coordinates: [lon, lat] } })
    }
  }
  return { type: 'FeatureCollection', features }
}

// The whole rain field as dots: at most one per RAIN_SPACING_PX on screen, the wettest
// cell of each bin (so a storm isn't averaged away).
export function rainDots(response, zoom) {
  if (!response?.cells?.length) return { type: 'FeatureCollection', features: [] }
  const step = Math.max(response.cell[1], RAIN_SPACING_PX / ((512 * 2 ** zoom) / 360))
  const bins = new Map()
  for (const cell of response.cells) {
    if (cell[2] < RAIN_MIN_MM) continue
    const key = `${Math.floor(cell[0] / step)},${Math.floor(cell[1] / step)}`
    const best = bins.get(key)
    if (!best || cell[2] > best[2]) bins.set(key, cell)
  }
  return {
    type: 'FeatureCollection',
    features: [...bins.values()].map(([lon, lat, value]) => ({
      type: 'Feature', properties: { value }, geometry: { type: 'Point', coordinates: [lon, lat] },
    })),
  }
}

// Wind arrows on the fire hexagons only, at most one per ARROW_SPACING_PX on screen.
export function fireArrows(fireCells, grid, zoom) {
  const pxPerDegree = (512 * 2 ** zoom) / 360
  const step = ARROW_SPACING_PX / pxPerDegree
  const bins = new Map()
  for (const h of fireCells) {
    const [lat, lon] = cellToLatLng(h)
    const key = `${Math.floor(lon / step)},${Math.floor(lat / step)}`
    if (bins.has(key)) continue
    const cell = grid.at(lon, lat)
    if (cell) bins.set(key, [lon, lat, cell[2], cell[3]])
  }
  return {
    type: 'FeatureCollection',
    features: [...bins.values()].map(([lon, lat, value, from]) => ({
      type: 'Feature',
      properties: { value, to: (from + 180) % 360 },
      geometry: { type: 'Point', coordinates: [lon, lat] },
    })),
  }
}

// Arrow pointing north, drawn once as an SDF icon so MapLibre can color it.
export function windArrowImage(size = 32) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')
  const c = size / 2
  ctx.fillStyle = '#000'
  ctx.beginPath()
  ctx.moveTo(c, 2) // tip
  ctx.lineTo(c + size * 0.28, size * 0.45)
  ctx.lineTo(c + size * 0.09, size * 0.45)
  ctx.lineTo(c + size * 0.09, size - 3)
  ctx.lineTo(c - size * 0.09, size - 3)
  ctx.lineTo(c - size * 0.09, size * 0.45)
  ctx.lineTo(c - size * 0.28, size * 0.45)
  ctx.closePath()
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const compass = (deg) => COMPASS[Math.round(deg / 45) % 8]

// Tooltip lines for a point: {layer id: grid} -> the value of each layer's cell there.
export function weatherTooltip(grids, lon, lat) {
  return Object.entries(grids)
    .map(([id, grid]) => {
      const cell = grid?.at(lon, lat)
      if (!cell) return null
      const v = cell[2]
      if (id === 'temperature') return `🌡 Max temperature: <b>${v.toFixed(1)} °C</b>`
      if (id === 'precipitation') return `🌧 Rain: <b>${v.toFixed(1)} mm</b>`
      return `💨 Wind: <b>${v.toFixed(1)} m/s</b> from ${compass(cell[3])}`
    })
    .filter(Boolean)
    .join('<br>')
}

// ------------------------------------------------------------------ fetching ---

// Rain at full resolution (0.1°) only from this zoom on; below it the whole area,
// averaged by the backend into coarser cells. Temperature and wind (0.5°) always come
// for the whole area: ~11 k cells, one request per day, reused while panning.
export const RAIN_DETAIL_ZOOM = 5.5
const SNAP_DEG = 5 // detailed rain: the view's bbox snapped outwards, so nearby pans hit the cache

export function weatherRequest(id, date, zoom, bounds) {
  if (id !== 'precipitation' || zoom < RAIN_DETAIL_ZOOM || !bounds) return { date }
  const snap = (v, f) => Math.max(-180, Math.min(180, f(v / SNAP_DEG) * SNAP_DEG))
  const bbox = [
    snap(bounds.getWest(), Math.floor), Math.max(-90, snap(bounds.getSouth(), Math.floor)),
    snap(bounds.getEast(), Math.ceil), Math.min(90, snap(bounds.getNorth(), Math.ceil)),
  ].join(',')
  return { date, bbox }
}

// Small LRU (of request promises): the slider replays days, panning reuses the same request.
export function responseCache(max = 120) {
  const entries = new Map()
  return {
    get(key) {
      const v = entries.get(key)
      if (v) { entries.delete(key); entries.set(key, v) }
      return v
    },
    set(key, value) {
      entries.set(key, value)
      if (entries.size > max) entries.delete(entries.keys().next().value)
    },
    delete(key) { entries.delete(key) },
  }
}
