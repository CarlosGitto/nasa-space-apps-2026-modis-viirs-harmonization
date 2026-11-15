// Map pieces shared by the main map (FireMap) and the MODIS | VIIRS curtain (CompareMap).
import * as maplibregl from 'maplibre-gl'
// MapLibre 6 finds its worker relative to its own module URL, which breaks once
// Vite bundles it. Import the (self-contained) worker as an asset instead.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { cellArea } from 'h3-js'
import { num } from './format'
import { i18n } from './i18n'

maplibregl.setWorkerUrl(maplibreWorkerUrl)

export { maplibregl }

export const STYLE = import.meta.env.VITE_MAP_STYLE ?? 'https://tiles.openfreemap.org/styles/positron'
export const POINTS_ZOOM = 7 // curtain: from this zoom on, individual detections instead of hexagons
// Main map: hexagons never go away; from this zoom on their border turns dashed,
// the fill fades and individual detections are drawn on top.
export const DETAIL_ZOOM = 10
export const POINTS_LIMIT = 5000
export const EMPTY = { type: 'FeatureCollection', features: [] }

// H3 resolution for the current zoom: coarser cells when zoomed out, the fixed
// res-6 grid (the platform's unit, ~36 km²) from BASE_ZOOM on.
export const BASE_RES = 6
export const BASE_ZOOM = 7.5
export const hexRes = (zoom) => (zoom < 3 ? 2 : zoom < 4.5 ? 3 : zoom < 6 ? 4 : zoom < BASE_ZOOM ? 5 : BASE_RES)

// Sensor colors for charts (validated categorical pair). MODIS and VIIRS are never summed.
export const SENSOR_COLORS = { modis: '#eb6834', viirs: '#2a78d6' }
export const SENSOR_LABELS = { modis: 'MODIS', viirs: 'VIIRS' }

export const fmt = (n) => num(n ?? 0, 1)

// Visible area as minLon,minLat,maxLon,maxLat; null when the whole world is visible.
// padKm widens it: the backend picks hexagons by their center, so pad by about one
// cell to also get the ones only partly on screen.
export function viewportBbox(map, padKm = 0) {
  const b = map.getBounds()
  const padLat = padKm / 111
  const padLon = padLat / Math.max(Math.cos((Math.min(Math.max(Math.abs(b.getSouth()), Math.abs(b.getNorth())), 85) * Math.PI) / 180), 0.1)
  const west = Math.max(b.getWest() - padLon, -180)
  const east = Math.min(b.getEast() + padLon, 180)
  const south = Math.max(b.getSouth() - padLat, -90)
  const north = Math.min(b.getNorth() + padLat, 90)
  if ((west <= -180 && east >= 180) || west >= east || south >= north) return null
  return [west, south, east, north].map((v) => v.toFixed(4)).join(',')
}

// Resolves once the map has drawn everything pending (data and tiles), or after maxMs.
export const whenIdle = (map, maxMs = 8000) =>
  new Promise((resolve) => {
    const done = () => { clearTimeout(timer); map.off('idle', done); resolve() }
    const timer = setTimeout(done, maxMs)
    map.on('idle', done)
    map.triggerRepaint() // an idle event comes even if nothing was pending
  })

// First label layer of the basemap: data goes below it so place names stay readable.
export const labelLayerId = (map) => map.getStyle().layers.find((l) => l.type === 'symbol')?.id

// Hexagon color: fire DENSITY (detections per 1,000 km², per day of the period), not a
// count: zoomed out one coarse hexagon (res 2, ~87,000 km²) adds up thousands of
// detections, and by count whole continents would saturate to red.
// The scale is RELATIVE to what was asked for: its stops are percentiles (log) of the
// densities of the query, so a long range (months, years) or a far zoom, where the
// average per day is small, still goes from pale to dark instead of all pale. Each
// feature gets `level` (0..1) from densityScale(); the paint only reads `level`.
// The curtain's two sides share one scale (computed from both), so they compare.
const LEVEL = ['coalesce', ['get', 'level'], 0]
export const FIRE_RAMP = ['#fef3c7', '#fde68a', '#fbbf24', '#f97316', '#dc2626', '#7f1d1d']
export const HEX_FILL_COLOR = ['interpolate', ['linear'], LEVEL, ...FIRE_RAMP.flatMap((c, i) => [i / (FIRE_RAMP.length - 1), c])]
// Opacity by level too: little activity reads as a faint tint, hot spots as solid.
export const hexOpacity = (max) => ['interpolate', ['linear'], LEVEL, 0, max * 0.35, 0.5, max * 0.7, 1, max]

// Anomalies (the calendar's and the map's): a value against the same dates of the other years. Violet below,
// neutral gray as usual, fire colors above: no blue, which reads as "the map cooled down". Lightness is close to
// symmetric on both arms (OKLab L .38/.61/.82 | .94 | .84/.65/.45). Levels: ¼× .. 1× .. 4× (log2, clamped).
export const ANOMALY_RAMP = ['#553190', '#8b74c2', '#c9bfe3', '#ecebe8', '#fbbf77', '#ea580c', '#9a1b1b']
export const HEX_ANOMALY_COLOR = ['interpolate', ['linear'], LEVEL, ...ANOMALY_RAMP.flatMap((c, i) => [i / (ANOMALY_RAMP.length - 1), c])]
// Opacity by how far from usual: a usual hexagon is a faint tint, big departures (either way) are solid.
export const anomalyOpacity = (max) => ['interpolate', ['linear'], ['coalesce', ['get', 'strength'], 0], 0, max * 0.2, 1, max]
export const ANOMALY_LOG2_CLAMP = 2
// A color x (0..1) along a ramp of hex stops, for HTML/SVG (the calendar's cells, the time slider's bars).
const rgb = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16))
export function rampColor(stops, x) {
  const p = Math.min(1, Math.max(0, x)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(p))
  const [a, b] = [rgb(stops[i]), rgb(stops[i + 1])]
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * (p - i))).join(',')})`
}
export const anomalyLevel = (ratio) =>
  (Math.max(-ANOMALY_LOG2_CLAMP, Math.min(ANOMALY_LOG2_CLAMP, Math.log2(Math.max(ratio, 1e-6)))) + ANOMALY_LOG2_CLAMP) / (2 * ANOMALY_LOG2_CLAMP)

const DAY_MS = 86_400_000
export const daysBetween = (start, end) => (start && end ? Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / DAY_MS) + 1) : 1)
const perArea = (f, field) => ((f.properties[field] ?? 0) / cellArea(f.properties.h3, 'km2')) * 1000
// Adds `density` (per 1,000 km² per day of start..end) to GET /stats/hex features: of detections, or of FRP (MW)
// when the color is fire power.
export function withDensity(fc, start, end, field = 'detections') {
  if (!fc?.features) return fc
  const days = daysBetween(start, end)
  for (const f of fc.features) f.properties.density = perArea(f, field) / days
  return fc
}

// Anomalies: each hexagon's detections against its typical (GET .../stats/hex/typical: the same dates of the other
// years, averaged). `share` = days shown / days of the typical's period (a day of the slider against its range).
// Hexagons that usually burn then but have nothing now are added (0 detections: the strongest "below").
// A pseudo-count of 1 keeps a single detection over a typical of 0.1 from reading as 10×.
export function withAnomaly(fc, typical, share = 1) {
  const years = typical?.years ?? 0
  const usual = new Map((typical?.features ?? []).map((f) => [f.properties.h3, f]))
  const features = [...(fc?.features ?? [])]
  const seen = new Set(features.map((f) => f.properties.h3))
  for (const [h3, f] of usual) {
    if (!seen.has(h3)) features.push({ ...f, properties: { h3, detections: 0, frp_sum: 0, static_detections: 0 } })
  }
  for (const f of features) {
    const p = f.properties
    if (!years) { Object.assign(p, { level: 0.5, strength: 0, ratio: null, typical: null }); continue }
    const typ = (usual.get(p.h3)?.properties.detections ?? 0) * share
    const ratio = (p.detections + 1) / (typ + 1)
    const level = anomalyLevel(ratio)
    Object.assign(p, { typical: typ, years, ratio, level, strength: Math.abs(level - 0.5) * 2 })
  }
  return { ...fc, features }
}

// Stops of the relative scale, in ln(density): p5, p50, p90 and p99 of the given
// densities -> levels 0, 0.45, 0.8 and 1. With few or very even values the low end is
// pulled down (at least a factor e³ ≈ 20 between ends), so a lone hexagon reads dark.
const LEVELS = [0, 0.45, 0.8, 1]
export function densityScale(densities) {
  const v = densities.filter((x) => x > 0).sort((a, b) => a - b)
  if (!v.length) return null
  const q = (p) => Math.log(v[Math.round(p * (v.length - 1))])
  const top = q(0.99)
  const stops = [Math.min(q(0.05), top - 3), q(0.5), q(0.9), top]
  for (let i = stops.length - 2; i >= 0; i--) stops[i] = Math.min(stops[i], stops[i + 1] - 0.05)
  return stops
}
const levelOf = (density, stops) => {
  if (!stops || !(density > 0)) return 0
  const x = Math.log(density)
  if (x <= stops[0]) return 0
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i]) return LEVELS[i - 1] + ((x - stops[i - 1]) / (stops[i] - stops[i - 1])) * (LEVELS[i] - LEVELS[i - 1])
  }
  return 1
}
// Puts `level` on every feature. `factor` rescales densities before comparing (the main
// map's time slider: a day's density against the scale of the whole range, see FireMap).
export function applyScale(fc, stops, factor = 1) {
  for (const f of fc?.features ?? []) f.properties.level = levelOf(f.properties.density * factor, stops)
  return fc
}
// Three legend values (low, middle, high) of a scale, in density units.
export const scaleTicks = (stops, factor = 1) => (stops ? [stops[0], stops[1], stops[3]].map((s) => Math.exp(s) / factor) : null)

// Hexagon borders: 1 CSS pixel, light. Thinner than a pixel (they were 0.3 px), a line is
// anti-aliased differently by every screen scale (100 %, 125 %, 200 %), browser zoom and
// GPU: dark on one computer, almost invisible on another. A whole pixel (1 device pixel at
// 1x, 2 at 2x) keeps the same weight everywhere; the opacity keeps it light.
export const HEX_BORDER_WIDTH = 1

// Heat that is not a wildfire (refineries, gas flares, industry, volcanoes) is drawn gray.
export const STATIC_COLOR = '#6b7280'

// Map tooltips, in the user's language. Every value inserted is a number, a date or an
// id from our own API, so the HTML stays safe.
const t = (key, values) => i18n.global.t(key, values)

// "35% more than usual" / "60% less than usual" / "as usual" (the calendar's wording).
const anomalyText = (ratio) => {
  const p = Math.round((ratio - 1) * 100)
  if (p === 0) return t('cal.asTypical')
  return `<b>${t(p > 0 ? 'cal.above' : 'cal.below', { p: num(Math.abs(p)) })}</b>`
}

// p.h3 is the cell's fixed H3 id: the same id is always the same place.
// `hint` tells what a click does (zoom in, or open the panel).
export const hexPopup = (p, hint = '') =>
  `<b>${fmt(p.detections)}</b> ${t('map.popup.detections')}<br>${t('map.popup.frpTotal')}: ${fmt(p.frp_sum)} MW<br>` +
  (p.density !== undefined ? `<span class="popup-muted">${t('map.popup.density', { n: fmt(p.density) })}</span><br>` : '') +
  (p.ratio !== undefined && p.ratio !== null ? `${anomalyText(p.ratio)}<br><span class="popup-muted">${t('map.popup.typical', { n: fmt(p.typical), years: p.years })}</span><br>` : '') +
  (p.static_detections ? `<span class="popup-static">${t('map.popup.static', { n: fmt(p.static_detections) })}</span><br>` : '') +
  `<span class="popup-muted">${t('map.popup.cell', { id: p.h3 })}</span>` +
  (hint ? `<br><span class="popup-muted">${hint}</span>` : '')

export const pointPopup = (p) => `
  ${p.is_static_source ? `<b class="popup-static">${t('map.popup.staticSource')}</b><br>` : ''}
  <b>${p.satellite ?? p.sensor}</b> · ${(p.product ?? '').toUpperCase()} · ${p.acq_datetime_utc.replace('T', ' ').slice(0, 16)} UTC<br>
  FRP: ${fmt(p.frp)} MW<br>
  ${t('map.popup.confidence')}: ${p.confidence ?? '–'} · ${p.daynight === 'D' ? t('map.popup.day') : p.daynight === 'N' ? t('map.popup.night') : '–'}`

// Basemap place names in the user's language (OpenMapTiles has name:es, name:en...),
// falling back to the local name.
export function localizeBasemap(map, locale) {
  for (const layer of map.getStyle().layers) {
    if (layer.type !== 'symbol' || !JSON.stringify(map.getLayoutProperty(layer.id, 'text-field') ?? '').includes('name')) continue
    map.setLayoutProperty(layer.id, 'text-field', ['coalesce', ['get', `name:${locale}`], ['get', 'name:latin'], ['get', 'name']])
  }
}

// Texts of MapLibre's own controls (zoom buttons...).
export const mapLocale = () => ({
  'NavigationControl.ZoomIn': i18n.global.t('map.zoomIn'),
  'NavigationControl.ZoomOut': i18n.global.t('map.zoomOut'),
})

// Zoom a map to a region's [w, s, e, n] (null = the whole world), animated unless the user asked for less motion.
export function fitRegion(map, box, reduceMotion) {
  const duration = reduceMotion ? 0 : 900
  if (!box) return map.easeTo({ center: [0, 20], zoom: 1.5, duration, essential: true })
  map.fitBounds([[box[0], box[1]], [box[2], box[3]]], { padding: 32, maxZoom: 7, duration, essential: true })
}
