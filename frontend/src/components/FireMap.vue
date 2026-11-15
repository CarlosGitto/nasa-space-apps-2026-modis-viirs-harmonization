<script setup>
// Map: H3 hexagons when zoomed out, individual detections when zoomed in.
// Always the whole world unless the user draws a zone (Terra Draw).
// Optional NASA GIBS satellite imagery under the data, for the end day of the selected range.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { TerraDraw, TerraDrawPolygonMode, TerraDrawRectangleMode } from 'terra-draw'
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter'
import { getDetections, getHexagons } from '../api'
import { gibsUrl } from '../gibs'
import {
  EMPTY, HEX_FILL_COLOR, POINTS_LIMIT, POINTS_ZOOM, STYLE,
  fmt, hexPopup, hexRes, labelLayerId, maplibregl, pointPopup, viewportBbox,
} from '../mapShared'
import { useFiltersStore } from '../stores/filters'

const store = useFiltersStore()
const container = ref(null)
const tool = ref(null) // 'polygon' | 'rectangle' | null
const loading = ref(false)
const error = ref(null)
const info = ref('')
const imageryNote = ref('') // warning when the chosen imagery has no picture for that date

let map = null
let mapReady = false // set on 'load'; isStyleLoaded() flips to false while sources update
let draw = null
let controller = null
let debounce = null

async function refresh() {
  // Hidden while the MODIS | VIIRS curtain is shown: don't fetch for nothing.
  if (!mapReady || store.view !== 'map' || !store.dataset || !store.currentDataset?.available) return
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  const zoom = map.getZoom()
  const params = { ...store.queryParams, bbox: viewportBbox(map) }
  loading.value = true
  try {
    if (zoom < POINTS_ZOOM) {
      const res = hexRes(zoom)
      const fc = await getHexagons(store.dataset, { ...params, res }, signal)
      map.getSource('hex').setData(fc)
      map.getSource('points').setData(EMPTY)
      const total = fc.features.reduce((s, f) => s + f.properties.detections, 0)
      info.value = `${fmt(total)} detections in ${fmt(fc.features.length)} hexagons (H3 res ${res})`
    } else {
      const fc = await getDetections(store.dataset, { ...params, limit: POINTS_LIMIT }, signal)
      map.getSource('points').setData(fc)
      map.getSource('hex').setData(EMPTY)
      info.value = fc.truncated
        ? `Showing the ${fmt(POINTS_LIMIT)} most recent detections: zoom in or shorten the dates to see all`
        : `${fmt(fc.features.length)} detections`
    }
    error.value = null
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e.message
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

function scheduleRefresh() {
  clearTimeout(debounce)
  debounce = setTimeout(refresh, 250)
}

function addLayers() {
  // Draw data below the basemap labels so country/city names stay readable.
  const labels = labelLayerId(map)

  // NASA GIBS satellite imagery: below the data, hidden until an image type is chosen.
  map.addSource('gibs', {
    type: 'raster',
    tileSize: 256,
    maxzoom: 9, // GIBS resolution; MapLibre upscales beyond this zoom
    tiles: [gibsUrl('VIIRS_NOAA20_CorrectedReflectance_TrueColor', '2025-01-01')], // replaced by syncImagery()
    attribution: 'Imagery: <a href="https://www.earthdata.nasa.gov/gibs">NASA EOSDIS GIBS</a>',
  })
  map.addLayer({
    id: 'gibs',
    type: 'raster',
    source: 'gibs',
    layout: { visibility: 'none' },
    // Darkened and desaturated so the fire data stands out.
    paint: { 'raster-brightness-max': 0.65, 'raster-saturation': -0.3 },
  }, labels)

  map.addSource('hex', { type: 'geojson', data: EMPTY })
  map.addSource('points', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'hex-fill',
    type: 'fill',
    source: 'hex',
    paint: {
      // Log scale: a few cells have thousands of detections, most have a handful.
      'fill-color': HEX_FILL_COLOR,
      'fill-opacity': 0.65,
    },
  }, labels)
  map.addLayer({
    id: 'hex-line',
    type: 'line',
    source: 'hex',
    paint: { 'line-color': '#7c2d12', 'line-width': 0.3, 'line-opacity': 0.4 },
  }, labels)
  map.addLayer({
    id: 'points',
    type: 'circle',
    source: 'points',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 12, 7],
      'circle-color': [
        'interpolate', ['linear'], ['coalesce', ['get', 'frp'], 0],
        0, '#fde047', 10, '#f97316', 50, '#dc2626', 200, '#7f1d1d',
      ],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 0.5,
      'circle-opacity': 0.9,
    },
  }, labels)
}

// Registered once: map.on('click', layerId, …) keeps working if the layer is re-created.
function registerEvents() {
  const popup = (layer, html) => {
    map.on('click', layer, (e) => {
      if (tool.value) return // drawing: clicks belong to Terra Draw
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html(e.features[0].properties)).addTo(map)
    })
    map.on('mouseenter', layer, () => { if (!tool.value) map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', layer, () => { if (!tool.value) map.getCanvas().style.cursor = '' })
  }
  popup('hex-fill', hexPopup)
  popup('points', pointPopup)
  map.on('moveend', () => store.setCamera(map.getCenter().toArray(), map.getZoom()))
  map.on('moveend', scheduleRefresh)
}

// Keep the GIBS layer in line with the chosen image type and the end of the date range.
function syncImagery() {
  if (!mapReady) return
  const img = store.imagery // { layer, date } or null
  map.setLayoutProperty('gibs', 'visibility', img ? 'visible' : 'none')
  if (img) map.getSource('gibs').setTiles([gibsUrl(img.layer.id, img.date)])
  imageryNote.value = store.basemap !== 'vector' && !img ? 'No satellite imagery for that date' : ''
}

function setupDraw() {
  const styles = {
    fillColor: '#2563eb', fillOpacity: 0.08,
    outlineColor: '#1d4ed8', outlineWidth: 2,
  }
  draw = new TerraDraw({
    adapter: new TerraDrawMapLibreGLAdapter({ map }),
    modes: [new TerraDrawPolygonMode({ styles }), new TerraDrawRectangleMode({ styles })],
  })
  draw.start()
  draw.on('finish', (id) => {
    const feature = draw.getSnapshotFeature(id)
    if (!feature) return
    // One zone at a time: drop the previous one.
    const others = draw.getSnapshot().filter((f) => f.id !== id).map((f) => f.id)
    if (others.length) draw.removeFeatures(others)
    stopDrawing()
    store.setZone(feature.geometry)
  })
}

function startDrawing(mode) {
  tool.value = mode
  draw.setMode(mode)
}

function stopDrawing() {
  tool.value = null
  draw.setMode('static')
}

function clearZone() {
  draw.clear()
  store.clearZone()
}

onMounted(() => {
  map = new maplibregl.Map({
    container: container.value,
    style: STYLE,
    center: store.camera.center, // kept in the store so the curtain view opens at the same place
    zoom: store.camera.zoom,
    renderWorldCopies: false,
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => {
    addLayers()
    registerEvents()
    setupDraw()
    mapReady = true
    syncImagery()
    refresh()
  })
})

onBeforeUnmount(() => {
  controller?.abort()
  clearTimeout(debounce)
  draw?.stop()
  map?.remove()
})

watch(() => [store.dataset, store.queryParams], refresh)
// Back from the curtain view: the container was hidden, so resize, catch up with
// where the curtain was left, and load what changed meanwhile.
watch(() => store.view, (view) => {
  if (view !== 'map' || !map) return
  requestAnimationFrame(() => {
    map.resize()
    map.jumpTo(store.camera)
    refresh()
  })
})
// Image type, satellite or day changed: update the imagery.
// Watch id and date separately: pickLayer() returns a new object every time.
watch(() => [store.basemap, store.imagery?.layer.id, store.imagery?.date], syncImagery)
// Zone cleared from elsewhere (e.g. the side panel): remove the drawing too.
watch(() => store.zone, (zone) => { if (!zone && draw?.getSnapshot().length) draw.clear() })
</script>

<template>
  <div class="map-wrap">
    <div ref="container" class="map"></div>

    <div class="toolbar">
      <button :class="{ active: tool === 'polygon' }" @click="tool === 'polygon' ? stopDrawing() : startDrawing('polygon')">
        ✏️ Polygon
      </button>
      <button :class="{ active: tool === 'rectangle' }" @click="tool === 'rectangle' ? stopDrawing() : startDrawing('rectangle')">
        ▭ Rectangle
      </button>
      <button v-if="store.zone" @click="clearZone">✕ Clear zone</button>
      <span v-if="tool" class="hint">
        {{ tool === 'polygon' ? 'Click to add vertices, double-click to close' : 'Click two corners' }}
      </span>
    </div>

    <div v-if="loading || error || info || imageryNote" class="status">
      <span v-if="loading" class="spinner" aria-label="Loading"></span>
      <span v-if="error" class="error">{{ error }}</span>
      <span v-else>{{ info }}</span>
      <span v-if="imageryNote" class="warn">{{ imageryNote }}</span>
    </div>
  </div>
</template>

<style scoped>
.map-wrap { position: relative; height: 100%; }
.map { position: absolute; inset: 0; }
.toolbar {
  position: absolute; top: 12px; left: 12px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
}
.toolbar button {
  background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 10px; font: inherit; font-size: 13px; cursor: pointer; box-shadow: var(--shadow);
}
.toolbar button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.hint {
  background: var(--panel); padding: 5px 8px; border-radius: 6px; font-size: 12px; color: var(--muted);
  box-shadow: var(--shadow);
}
.status {
  position: absolute; bottom: 28px; left: 12px; right: 12px; max-width: fit-content; display: flex; gap: 8px;
  align-items: center; flex-wrap: wrap; background: var(--panel); padding: 6px 10px; border-radius: 6px;
  font-size: 13px; box-shadow: var(--shadow);
}
.error { color: var(--danger); }
.warn { color: var(--muted); }
.spinner {
  width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 2.4s; } }
</style>