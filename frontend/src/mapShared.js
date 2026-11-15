// Map pieces shared by the main map (FireMap) and the MODIS | VIIRS curtain (CompareMap).
import * as maplibregl from 'maplibre-gl'
// MapLibre 6 finds its worker relative to its own module URL, which breaks once
// Vite bundles it. Import the (self-contained) worker as an asset instead.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
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

// Hexagon fill: fixed log scale (not relative to what's on screen), so the same
// color means the same number of detections everywhere, on both sides of the curtain.
// max(1, …) avoids ln(0) and missing values.
export const HEX_FILL_COLOR = [
  'interpolate', ['linear'], ['ln', ['max', 1, ['coalesce', ['get', 'detections'], 1]]],
  0, '#fde68a', 2, '#fbbf24', 4, '#f97316', 6, '#dc2626', 8, '#7f1d1d',
]

// Heat that is not a wildfire (refineries, gas flares, industry, volcanoes) is drawn gray.
export const STATIC_COLOR = '#6b7280'

// Map tooltips, in the user's language. Every value inserted is a number, a date or an
// id from our own API, so the HTML stays safe.
const t = (key, values) => i18n.global.t(key, values)

// p.h3 is the cell's fixed H3 id: the same id is always the same place.
// `hint` tells what a click does (zoom in, or open the panel).
export const hexPopup = (p, hint = '') =>
  `<b>${fmt(p.detections)}</b> ${t('map.popup.detections')}<br>${t('map.popup.frpTotal')}: ${fmt(p.frp_sum)} MW<br>` +
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
