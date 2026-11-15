// Weather layers of the main map (GET /weather/map): one UTC day of temperature, rain
// and wind, as icons (Material Design Icons) that never paint the ground, so the fire
// hexagons stay the protagonists:
//   thermometer colored by the daily max temperature (usual temperature-map colors)
//   drop sized by the daily rain (mm); dry places have none
//   arrow rotated to where the wind blows, sized and colored by its speed
// Icons of a place are grouped (thermometer left, drop right, arrow in the middle) and
// thinned to one group per ICON_SPACING_PX on screen. Groups go on the neighbors of the
// fire hexagons, not on them: the fire itself stays uncovered.
//
// Two scopes:
// - "fires" (default): around the fire hexagons on screen, out to FADE_RINGS rings of
//   neighbors, fading with the distance to the fire: the data goes on past the
//   neighbors, it's just shown softer. Built in the browser from the hexagons already
//   loaded and one cached grid per layer and day: panning and the slider don't query
//   the lake again.
// - "all": the same icons over the whole loaded area.
import { mdiArrowUpBold, mdiThermometer, mdiWater } from '@mdi/js'
import { cellToLatLng, gridDiskDistances } from 'h3-js'
import { num } from './format'
import { i18n } from './i18n'

// Names in the user's language: getters, so templates that read them follow a language change.
const t = (key, values) => i18n.global.t(key, values)

export const WEATHER_LAYERS = {
  temperature: {
    get label() { return t('weather.layers.temperature') },
    get title() { return t('weather.kpi.tmax') },
    unit: '°',
    icon: mdiThermometer,
    // The usual temperature-map palette (blue -> cyan -> green -> yellow -> orange -> red):
    // only the thermometer is colored, never the ground, so it doesn't compete with fire.
    stops: [[-10, '#3b4cc0'], [0, '#4f8fd1'], [10, '#5fc4c0'], [18, '#9ad35e'], [24, '#f2e24a'],
            [30, '#f59e2c'], [36, '#e0412b'], [42, '#8b0000']],
    legend: [0, 10, 20, 30, 40],
  },
  precipitation: {
    get label() { return t('weather.layers.precipitation') },
    get title() { return t('weather.layers.precipitation') },
    unit: ' mm',
    icon: mdiWater,
    color: '#2563eb',
    legend: [1, 5, 20],
  },
  wind: {
    get label() { return t('weather.layers.wind') },
    get title() { return t('weather.layers.wind') },
    unit: ' m/s',
    icon: mdiArrowUpBold,
    // Dark from the start (light blues got lost on the light basemap): slate -> blue -> violet.
    stops: [[0, '#475569'], [3, '#0369a1'], [7, '#1d4ed8'], [12, '#4338ca'], [18, '#6b21a8']],
    legend: [2, 7, 14],
  },
}
export const WEATHER_IDS = Object.keys(WEATHER_LAYERS)

const ICON_SPACING_PX = 72
const FADE_RINGS = 3 // fire hexagon (ring 0) + its neighbors out to this ring
const FADE = [0.9, 1, 0.65, 0.35] // icon opacity by ring
// Which ring a bin prefers for its icon group: next to the fire, then further out, and
// on a fire hexagon only when nothing else falls in the bin.
const RING_PREFERENCE = [9, 0, 1, 2]
const MAX_FIRE_CELLS_FOR_FULL_FADE = 1500 // past it, only the first ring (keeps it fast)
export const RAIN_MIN_MM = 0.1

// --------------------------------------------------------------- styling ---

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))

// CSS color of a layer's ramp at a value (legends and panel).
export function colorAt(id, v) {
  const stops = WEATHER_LAYERS[id].stops
  if (v <= stops[0][0]) return stops[0][1]
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1] = stops[i]
    if (v <= v1) {
      const [v0, c0] = stops[i - 1]
      const t = (v - v0) / (v1 - v0)
      const [a, b] = [rgb(c0), rgb(c1)]
      return `rgb(${a.map((c, k) => Math.round(c + (b[k] - c) * t)).join(',')})`
    }
  }
  return stops.at(-1)[1]
}

// MapLibre expressions over the point's properties (t, rain, wind).
export const colorRamp = (id, prop) => ['interpolate', ['linear'], ['get', prop], ...WEATHER_LAYERS[id].stops.flat()]
export const TEMPERATURE_SIZE = 0.5
export const RAIN_SIZE = ['interpolate', ['linear'], ['sqrt', ['get', 'rain']], 0, 0.32, Math.sqrt(50), 0.8]
export const rainSize = (mm) => 0.32 + (Math.sqrt(Math.min(mm, 50)) / Math.sqrt(50)) * 0.48
export const WIND_SIZE = ['interpolate', ['linear'], ['get', 'wind'], 0, 0.55, 15, 1.1]
export const windSize = (v) => 0.55 + (Math.min(v, 15) / 15) * 0.55

// An MDI icon (24x24 path) drawn on a canvas, for map.addImage(..., { sdf: true }):
// MapLibre then colors it per feature (icon-color) and adds a halo.
export function iconImage(path, size = 48) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')
  ctx.scale(size / 24, size / 24)
  ctx.fillStyle = '#000'
  ctx.fill(new Path2D(path))
  return ctx.getImageData(0, 0, size, size)
}

// ------------------------------------------------------------------ grids ---

// A response's cells as a dense grid, for lookups by position.
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
    response,
    // The cell under a point: [lon, lat, value(, from)], or null.
    at(lon, lat) {
      const c = Math.round((lon - minLon) / dlon)
      const r = Math.round((lat - minLat) / dlat)
      if (c < 0 || r < 0 || c >= cols || r >= rows) return null
      const i = index[r * cols + c]
      return i < 0 ? null : response.cells[i]
    },
  }
}

// ----------------------------------------------------------------- points ---

// Hexagons around the fires, each with its ring distance to the nearest fire hexagon:
// Map(h3 -> ring).
export function fireNeighborhood(fireCells) {
  const rings = fireCells.length > MAX_FIRE_CELLS_FOR_FULL_FADE ? 1 : FADE_RINGS
  const out = new Map()
  for (const h of fireCells) {
    gridDiskDistances(h, rings).forEach((ring, k) => {
      for (const n of ring) if (!out.has(n) || out.get(n) > k) out.set(n, k)
    })
  }
  return out
}

// Candidates around fires: the center of each hexagon of the neighborhood.
export const fireCandidates = (neighborhood) =>
  [...neighborhood].map(([h, ring]) => {
    const [lat, lon] = cellToLatLng(h)
    return { lon, lat, ring }
  })

// Candidates everywhere: the cells of a loaded grid (all layers share POWER's grid).
export function gridCandidates(grids) {
  const grid = grids.temperature ?? grids.wind ?? grids.precipitation
  return grid ? grid.response.cells.map(([lon, lat]) => ({ lon, lat, ring: 0 })) : []
}

// One icon group per ICON_SPACING_PX bin: the candidate with the preferred ring wins (see
// RING_PREFERENCE; then the one nearest to the bin's center), with the value of each
// layer's cell under it.
// candidates: [{ lon, lat, ring }]. grids: { layer id: grid }.
export function weatherPoints(candidates, grids, zoom) {
  const step = ICON_SPACING_PX / ((512 * 2 ** zoom) / 360)
  const bins = new Map()
  for (const c of candidates) {
    const bx = Math.floor(c.lon / step)
    const by = Math.floor(c.lat / step)
    const d = (c.lon - (bx + 0.5) * step) ** 2 + (c.lat - (by + 0.5) * step) ** 2
    const key = `${bx},${by}`
    const best = bins.get(key)
    const rank = RING_PREFERENCE[c.ring] ?? c.ring
    if (!best || rank < best.rank || (rank === best.rank && d < best.d)) bins.set(key, { ...c, d, rank })
  }
  const features = []
  for (const { lon, lat, ring } of bins.values()) {
    const p = { fade: FADE[ring] ?? FADE.at(-1) }
    const t = grids.temperature?.at(lon, lat)
    if (t) p.t = t[2]
    const r = grids.precipitation?.at(lon, lat)
    if (r && r[2] >= RAIN_MIN_MM) p.rain = r[2]
    const w = grids.wind?.at(lon, lat)
    if (w) {
      p.wind = w[2]
      p.to = (w[3] + 180) % 360
    }
    if (p.t !== undefined || p.rain !== undefined || p.wind !== undefined) {
      features.push({ type: 'Feature', properties: p, geometry: { type: 'Point', coordinates: [lon, lat] } })
    }
  }
  return { type: 'FeatureCollection', features }
}

// ------------------------------------------------------- selected hexagons ---

// The day of a selection's weather to show for the map's day: that day, or the latest
// before it with any value (POWER is ~2 days behind: the end of the range, often today,
// has none yet). Null if there is none.
export function selectionDay(selection, day) {
  const rows = (selection?.days ?? []).filter(([d, values]) => d <= day && values.some((v) => v.some((x) => x !== null)))
  return rows.length ? rows[rows.length - 1][0] : null
}

// POST /hexagons/selection/weather -> one grid per layer for one day (same shape as the
// lake's grids, so weatherPoints and the tooltip use them alike).
export function selectionGrids(selection, day) {
  const row = selection?.days.find(([d]) => d === day)
  if (!row) return {}
  const [, values] = row
  const cells = (pick) => values
    .map((v, i) => (pick(v) ? [...selection.cells[i], ...pick(v)] : null))
    .filter(Boolean)
  const grid = (cs) => (cs.length ? weatherGrid({ cell: selection.cell, cells: cs }) : null)
  return {
    temperature: grid(cells(([t]) => (t === null ? null : [t]))),
    precipitation: grid(cells(([, r]) => (r === null ? null : [r]))),
    wind: grid(cells(([, , s, d]) => (s === null || d === null ? null : [s, d]))),
  }
}

// One lookup per layer: the selection's value where it has one, else the lake's.
export function mergedGrids(first, second) {
  const out = {}
  for (const id of WEATHER_IDS) {
    const a = first[id]
    const b = second[id]
    if (a || b) out[id] = { response: (a ?? b).response, at: (lon, lat) => a?.at(lon, lat) ?? b?.at(lon, lat) ?? null }
  }
  return out
}

// ---------------------------------------------------------------- tooltip ---

// Where the wind comes from, as a compass point in the user's language (0° = north).
const COMPASS = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw']
export const compass = (deg) =>
  (deg === null || deg === undefined ? '–' : t(`weather.compass.${COMPASS[Math.round(deg / 45) % 8]}`))

// Tooltip lines for a point: the value of each layer's cell there (numbers are ours, safe as HTML).
export function weatherTooltip(grids, lon, lat) {
  return Object.entries(grids)
    .map(([id, grid]) => {
      const cell = grid?.at(lon, lat)
      if (!cell) return null
      const v = `<b>${num(cell[2], 1)}</b>`
      if (id === 'temperature') return `🌡 ${t('weather.tooltip.temperature', { v })}`
      if (id === 'precipitation') return `💧 ${t('weather.tooltip.rain', { v })}`
      return `➤ ${t('weather.tooltip.wind', { v, from: compass(cell[3]) })}`
    })
    .filter(Boolean)
    .join('<br>')
}

// ------------------------------------------------------------------ fetching ---

// Every layer comes for the whole loaded area (NASA POWER daily near fires, on the
// MERRA-2 grid: a few thousand cells): one request per layer and day, reused while panning.
export function weatherRequest(id, date) {
  return { date }
}

// Small LRU (of request promises): the slider replays days, panning reuses the same request.
export function responseCache(max = 120) {
  const entries = new Map()
  return {
    get(key) {
      const v = entries.get(key)
      if (v) {
        entries.delete(key)
        entries.set(key, v)
      }
      return v
    },
    set(key, value) {
      entries.set(key, value)
      if (entries.size > max) entries.delete(entries.keys().next().value)
    },
    delete(key) {
      entries.delete(key)
    },
  }
}
