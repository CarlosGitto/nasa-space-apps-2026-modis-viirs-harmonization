// Map pieces shared by the main map (FireMap) and the MODIS | VIIRS curtain (CompareMap).
import * as maplibregl from 'maplibre-gl'
// MapLibre 6 finds its worker relative to its own module URL, which breaks once
// Vite bundles it. Import the (self-contained) worker as an asset instead.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'

maplibregl.setWorkerUrl(maplibreWorkerUrl)

export { maplibregl }

export const STYLE = import.meta.env.VITE_MAP_STYLE ?? 'https://tiles.openfreemap.org/styles/positron'
export const POINTS_ZOOM = 7 // from this zoom on, show individual detections
export const POINTS_LIMIT = 5000
export const EMPTY = { type: 'FeatureCollection', features: [] }

// H3 resolution for the current zoom: coarser cells when zoomed out.
export const hexRes = (zoom) => (zoom < 3 ? 2 : zoom < 5 ? 3 : 4)

export const fmt = (n) => (n ?? 0).toLocaleString('en-US', { maximumFractionDigits: 1 })

// Visible area as minLon,minLat,maxLon,maxLat; null when the whole world is visible.
export function viewportBbox(map) {
  const b = map.getBounds()
  const west = Math.max(b.getWest(), -180)
  const east = Math.min(b.getEast(), 180)
  const south = Math.max(b.getSouth(), -90)
  const north = Math.min(b.getNorth(), 90)
  if ((west <= -180 && east >= 180) || west >= east || south >= north) return null
  return [west, south, east, north].map((v) => v.toFixed(4)).join(',')
}

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

// p.h3 is the cell's fixed H3 id: the same id is always the same place.
export const hexPopup = (p) =>
  `<b>${fmt(p.detections)}</b> detections<br>Total FRP: ${fmt(p.frp_sum)} MW<br>` +
  (p.static_detections ? `<span style="color:${STATIC_COLOR}">+ ${fmt(p.static_detections)} static-source detections (not wildfires)</span><br>` : '') +
  `<span style="color:#6b7280">Cell ${p.h3}</span>`

export const pointPopup = (p) => `
  ${p.is_static_source ? `<b style="color:${STATIC_COLOR}">Static source (not a wildfire)</b><br>` : ''}
  <b>${p.satellite ?? p.sensor}</b> · ${(p.product ?? '').toUpperCase()} · ${p.acq_datetime_utc.replace('T', ' ').slice(0, 16)} UTC<br>
  FRP: ${fmt(p.frp)} MW<br>
  Confidence: ${p.confidence ?? '–'} · ${p.daynight === 'D' ? 'Day' : p.daynight === 'N' ? 'Night' : '–'}`
