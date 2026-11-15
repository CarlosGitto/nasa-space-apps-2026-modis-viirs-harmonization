// Contour lines (curvas de nivel), drawn in the browser from public elevation tiles:
// AWS Terrain Tiles (Terrarium PNGs, mostly NASA SRTM). Nothing is ingested or stored:
// maplibre-contour turns the visible elevation tiles into contour vector tiles in a
// web worker. Off (the default), nothing is requested; on, only the visible area.
//
// Telling them apart from the hexagons: contours are olive, curved and spaced out by
// zoom, and the hexagon borders turn neutral gray while they are on (FireMap.vue).
import mlcontour from 'maplibre-contour'
import { maplibregl } from './mapShared'

const TERRARIUM = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
const ATTRIBUTION = 'Elevation: <a href="https://registry.opendata.aws/terrain-tiles/">SRTM · AWS Terrain Tiles</a>'
const LAYERS = ['contour-lines', 'contour-labels']
const CONTOURS_MINZOOM = 6 // below this, a continent of lines is just noise
const DEM_MAXZOOM = 12 // ~30 m pixels: enough for 50 m contours

let dem = null

// One elevation source for the whole app, registered with MapLibre the first time the
// contours are turned on (its worker starts then, not on page load).
function demSource() {
  if (!dem) {
    dem = new mlcontour.DemSource({
      url: TERRARIUM,
      encoding: 'terrarium',
      maxzoom: DEM_MAXZOOM,
      worker: true,
      cacheSize: 100,
      timeoutMs: 10_000,
    })
    dem.setupMaplibre(maplibregl)
  }
  return dem
}

// Contour lines under the fire hexagons (data stays on top); labels under the detection
// points.
function addContours(map, { linesBelow, labelsBelow }) {
  map.addSource('contours', {
    type: 'vector',
    // Interval by zoom: [minor, major] in meters; majors are thicker and labeled.
    tiles: [demSource().contourProtocolUrl({
      // Spaced out by zoom so they never turn into scribbles.
      thresholds: { 6: [1000, 2000], 9: [500, 1000], 11: [100, 500], 13: [50, 250] },
      contourLayer: 'contours',
      elevationKey: 'ele',
      levelKey: 'level',
    })],
    minzoom: CONTOURS_MINZOOM,
    maxzoom: 14,
    attribution: ATTRIBUTION,
  })
  map.addLayer({
    id: 'contour-lines',
    type: 'line',
    source: 'contours',
    'source-layer': 'contours',
    minzoom: CONTOURS_MINZOOM,
    layout: { visibility: 'none' },
    paint: {
      'line-color': '#5f7a45',
      'line-opacity': ['match', ['get', 'level'], 1, 0.75, 0.4],
      'line-width': ['match', ['get', 'level'], 1, 1.2, 0.5],
    },
  }, linesBelow)
  map.addLayer({
    id: 'contour-labels',
    type: 'symbol',
    source: 'contours',
    'source-layer': 'contours',
    minzoom: CONTOURS_MINZOOM,
    filter: ['>', ['get', 'level'], 0], // only the major lines carry a label
    layout: {
      visibility: 'none',
      'symbol-placement': 'line',
      'text-field': ['concat', ['number-format', ['get', 'ele'], {}], ' m'],
      'text-font': ['Noto Sans Regular'],
      'text-size': 10,
    },
    paint: { 'text-color': '#3f5530', 'text-halo-color': '#ffffff', 'text-halo-width': 1.2 },
  }, labelsBelow)
}

// Show or hide the contours; the source and its worker are created on first use.
export function setContours(map, visible, placement) {
  if (visible && !map.getSource('contours')) addContours(map, placement)
  if (!map.getSource('contours')) return
  for (const id of LAYERS) map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
}

// High contrast (accessibility menu): darker, thicker lines.
export function contourContrast(map, on) {
  if (!map.getLayer('contour-lines')) return
  map.setPaintProperty('contour-lines', 'line-color', on ? '#2f4220' : '#5f7a45')
  map.setPaintProperty('contour-lines', 'line-width', ['match', ['get', 'level'], 1, on ? 1.7 : 1.2, on ? 0.9 : 0.5])
}
