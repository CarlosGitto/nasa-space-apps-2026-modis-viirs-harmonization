<script setup>
// MODIS | VIIRS curtain: two synced maps stacked on top of each other. MODIS on the
// left, VIIRS on the right, same product (NRT or SP) as the chosen dataset. The top
// (VIIRS) map is clipped from the handle to the right, so dragging the handle over a
// fire front shows how each sensor sees it. Each side can show its own satellite
// imagery for the selected day. Dates, zone and the time slider apply to both sides.
//
// Hexagons use the same fixed color scale on both sides, so a cell that looks lighter
// on the MODIS side really has fewer detections. Zoomed in, detections are drawn per
// sensor: MODIS = yellow ring (~1 km pixel), VIIRS = cyan dot (375 m pixel); static
// heat sources (refineries, gas flares...) in gray on both.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getDetections, getHexagons } from '../api'
import { GIBS_LAYERS, gibsDate, gibsUrl } from '../gibs'
import { cellsOutline } from '../hexGrid'
import {
  EMPTY, HEX_FILL_COLOR, POINTS_LIMIT, POINTS_ZOOM, STATIC_COLOR, STYLE,
  fmt, hexPopup, hexRes, labelLayerId, maplibregl, pointPopup, viewportBbox, whenIdle,
} from '../mapShared'
import { useFiltersStore } from '../stores/filters'

const LEFT_IMAGERY = [
  { id: 'MODIS_Aqua_CorrectedReflectance_Bands721', label: 'MODIS Aqua 7-2-1 (fire)' },
  { id: 'MODIS_Aqua_CorrectedReflectance_TrueColor', label: 'MODIS Aqua true color' },
  { id: 'MODIS_Terra_CorrectedReflectance_Bands721', label: 'MODIS Terra 7-2-1 (fire)' },
  { id: 'MODIS_Terra_CorrectedReflectance_TrueColor', label: 'MODIS Terra true color' },
  { id: 'none', label: 'Map only' },
]
const RIGHT_IMAGERY = [
  { id: 'VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1', label: 'VIIRS NOAA-20 M11-I2-I1 (fire)' },
  { id: 'VIIRS_NOAA20_CorrectedReflectance_TrueColor', label: 'VIIRS NOAA-20 true color' },
  { id: 'VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1', label: 'VIIRS S-NPP M11-I2-I1 (fire)' },
  { id: 'VIIRS_SNPP_CorrectedReflectance_TrueColor', label: 'VIIRS S-NPP true color' },
  { id: 'none', label: 'Map only' },
]
const MODIS_COLOR = '#ffcc00'
const VIIRS_COLOR = '#22d3ee'

const store = useFiltersStore()
const leftEl = ref(null)
const rightEl = ref(null)
const split = ref(50) // curtain position, % of the width
const imgLeft = ref(LEFT_IMAGERY[0].id)
const imgRight = ref(RIGHT_IMAGERY[0].id)
const detMode = ref('side') // 'side' | 'both' | 'none'
const loading = ref(false)
const error = ref(null)
const zoomedIn = ref(false)
const result = ref(null) // last fetch: { kind: 'hex' | 'points', modis, viirs }

const maps = { left: null, right: null }
let loaded = 0
let syncing = false
let controller = null
let debounce = null

const product = computed(() => store.compareSides.product.toUpperCase())

// ---------------------------------------------------------------- maps ---

function createMap(el, side) {
  const map = new maplibregl.Map({
    container: el,
    style: STYLE,
    center: store.camera.center,
    zoom: store.camera.zoom,
    renderWorldCopies: false,
    attributionControl: side === 'right' ? { compact: true } : false,
  })
  if (side === 'right') map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => {
    addLayers(map)
    if (++loaded === 2) onBothLoaded()
  })
  return map
}

function addLayers(map) {
  const labels = labelLayerId(map)
  map.addSource('img', { type: 'raster', tileSize: 256, maxzoom: 9, tiles: [gibsUrl(GIBS_LAYERS[0].id, '2025-01-01')],
    attribution: 'Imagery: <a href="https://www.earthdata.nasa.gov/gibs">NASA EOSDIS GIBS</a>' })
  map.addLayer({ id: 'img', type: 'raster', source: 'img', layout: { visibility: 'none' },
    paint: { 'raster-brightness-max': 0.8 } }, labels)
  for (const id of ['hex', 'modis', 'viirs', 'zone']) map.addSource(id, { type: 'geojson', data: EMPTY })
  map.addLayer({ id: 'hex-fill', type: 'fill', source: 'hex',
    paint: { 'fill-color': HEX_FILL_COLOR, 'fill-opacity': 0.7 } }, labels)
  map.addLayer({ id: 'hex-line', type: 'line', source: 'hex',
    paint: { 'line-color': '#7c2d12', 'line-width': 0.3, 'line-opacity': 0.4 } }, labels)
  // Like the prototype: VIIRS = solid cyan dot, MODIS = yellow ring with a dark halo (never color alone).
  map.addLayer({ id: 'viirs-pt', type: 'circle', source: 'viirs', paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 2.6, 10, 4.5, 13, 7],
    'circle-color': ['case', ['==', ['get', 'is_static_source'], true], STATIC_COLOR, VIIRS_COLOR],
    'circle-stroke-color': '#000', 'circle-stroke-width': 1.2 } })
  const ring = ['interpolate', ['linear'], ['zoom'], 7, 5, 10, 9, 13, 16]
  map.addLayer({ id: 'modis-halo', type: 'circle', source: 'modis', paint: {
    'circle-radius': ring, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#000', 'circle-stroke-width': 5 } })
  map.addLayer({ id: 'modis-pt', type: 'circle', source: 'modis', paint: {
    'circle-radius': ring, 'circle-color': 'rgba(0,0,0,0)',
    'circle-stroke-color': ['case', ['==', ['get', 'is_static_source'], true], STATIC_COLOR, MODIS_COLOR],
    'circle-stroke-width': 2.5 } })
  map.addLayer({ id: 'zone-line', type: 'line', source: 'zone',
    paint: { 'line-color': '#2563eb', 'line-width': 2, 'line-dasharray': [3, 2] } })

  for (const [layer, html] of [['hex-fill', hexPopup], ['modis-pt', pointPopup], ['viirs-pt', pointPopup]]) {
    map.on('click', layer, (e) => new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html(e.features[0].properties)).addTo(map))
    map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = '' })
  }
}

function onBothLoaded() {
  // Moving either map moves the other: they look like a single map.
  for (const [a, b] of [[maps.left, maps.right], [maps.right, maps.left]]) {
    a.on('move', () => {
      if (syncing) return
      syncing = true
      b.jumpTo({ center: a.getCenter(), zoom: a.getZoom(), bearing: a.getBearing(), pitch: a.getPitch() })
      syncing = false
    })
  }
  maps.left.on('moveend', () => {
    store.setCamera(maps.left.getCenter().toArray(), maps.left.getZoom())
    clearTimeout(debounce)
    debounce = setTimeout(refresh, 250)
  })
  syncImagery()
  syncZone()
  refresh()
}

const bothReady = () => loaded === 2

// ---------------------------------------------------------------- data ---

async function refresh() {
  if (!bothReady()) return
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  const zoom = maps.left.getZoom()
  zoomedIn.value = zoom >= POINTS_ZOOM
  const params = { ...store.queryParams, bbox: viewportBbox(maps.left) }
  const { left, right } = store.compareSides
  const fetchSide = (ds) => {
    if (!ds.available) return Promise.resolve(null)
    return zoomedIn.value
      ? getDetections(ds.id, { ...params, limit: POINTS_LIMIT }, signal)
      : getHexagons(ds.id, { ...params, res: hexRes(zoom) }, signal)
  }
  loading.value = true
  store.setBusy('curtain', true)
  try {
    const [modis, viirs] = await Promise.all([fetchSide(left), fetchSide(right)])
    result.value = { kind: zoomedIn.value ? 'points' : 'hex', modis, viirs }
    error.value = null
    render()
    // Both sides fully drawn (data and imagery) before the time slider moves on.
    await Promise.all([whenIdle(maps.left), whenIdle(maps.right)])
    if (!signal.aborted) store.setBusy('curtain', false)
  } catch (e) {
    if (e.name === 'AbortError') return // a newer refresh owns the busy flag
    error.value = e.message
    store.setBusy('curtain', false)
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

// Put the last fetch on each side according to the detections mode (no refetch).
function render() {
  if (!bothReady() || !result.value) return
  const { kind, modis, viirs } = result.value
  const fc = (r) => r ?? EMPTY
  const hide = detMode.value === 'none'
  const both = detMode.value === 'both'
  for (const side of ['left', 'right']) {
    const map = maps[side]
    const own = side === 'left' ? modis : viirs
    if (kind === 'hex') {
      // Hexagons of two sensors can't be overlaid: each side shows its own sensor.
      map.getSource('hex').setData(hide ? EMPTY : fc(own))
      map.getSource('modis').setData(EMPTY)
      map.getSource('viirs').setData(EMPTY)
    } else {
      map.getSource('hex').setData(EMPTY)
      map.getSource('modis').setData(!hide && (both || side === 'left') ? fc(modis) : EMPTY)
      map.getSource('viirs').setData(!hide && (both || side === 'right') ? fc(viirs) : EMPTY)
    }
  }
}

const total = (r) => {
  if (!r) return null
  return result.value?.kind === 'hex' ? r.features.reduce((s, f) => s + f.properties.detections, 0) : r.features.length
}
const counts = computed(() => ({ modis: total(result.value?.modis), viirs: total(result.value?.viirs) }))
const summary = computed(() => {
  const { modis, viirs } = counts.value
  const parts = [
    `MODIS ${modis === null ? 'no data' : fmt(modis)}`,
    `VIIRS ${viirs === null ? 'no data' : fmt(viirs)}`,
  ]
  if (modis && viirs) parts.push(`VIIRS/MODIS ×${(viirs / modis).toFixed(1)}`)
  return `${parts.join(' · ')} detections in view${store.compareHarmonized && result.value?.kind === 'hex' ? ' (MODIS units)' : ''}`
})
const truncated = computed(() => result.value?.kind === 'points' && (result.value.modis?.truncated || result.value.viirs?.truncated))

// ------------------------------------------------------------- imagery ---

const imageryState = (id) => {
  if (id === 'none') return { tiles: null, note: 'map only' }
  const layer = GIBS_LAYERS.find((l) => l.id === id)
  const date = layer && gibsDate(layer, store.imageryDate)
  return date ? { tiles: gibsUrl(id, date), note: date } : { tiles: null, note: 'no imagery for this date' }
}
const leftNote = computed(() => imageryState(imgLeft.value).note)
const rightNote = computed(() => imageryState(imgRight.value).note)

function syncImagery() {
  if (!bothReady()) return
  for (const [side, id] of [['left', imgLeft.value], ['right', imgRight.value]]) {
    const { tiles } = imageryState(id)
    maps[side].setLayoutProperty('img', 'visibility', tiles ? 'visible' : 'none')
    if (tiles) maps[side].getSource('img').setTiles([tiles])
  }
}

function syncZone() {
  if (!bothReady()) return
  // A drawn area is shown snapped to its hexagons, like on the main map.
  const geometry = store.zoneCells.length ? cellsOutline(store.zoneCells) : store.zone
  const data = geometry ? { type: 'Feature', geometry, properties: {} } : EMPTY
  maps.left.getSource('zone').setData(data)
  maps.right.getSource('zone').setData(data)
}

// ------------------------------------------------------------ lifecycle ---

onMounted(() => {
  maps.left = createMap(leftEl.value, 'left')
  maps.right = createMap(rightEl.value, 'right')
})

onBeforeUnmount(() => {
  controller?.abort()
  clearTimeout(debounce)
  store.setBusy('curtain', false)
  maps.left?.remove()
  maps.right?.remove()
})

watch(() => [store.queryParams, store.compareSides.left.id, store.compareSides.right.id], refresh)
watch(detMode, render)
watch(() => [imgLeft.value, imgRight.value, store.imageryDate], syncImagery)
watch(() => [store.zone, store.zoneCells], syncZone)
</script>

<template>
  <div class="compare">
    <div class="bar">
      <b>MODIS | VIIRS · {{ product }}</b>
      <label>Left
        <select v-model="imgLeft">
          <option v-for="o in LEFT_IMAGERY" :key="o.id" :value="o.id">{{ o.label }}</option>
        </select>
      </label>
      <label>Right
        <select v-model="imgRight">
          <option v-for="o in RIGHT_IMAGERY" :key="o.id" :value="o.id">{{ o.label }}</option>
        </select>
      </label>
      <label>Values
        <select v-model="store.compareHarmonized">
          <option :value="false">Raw</option>
          <option :value="true">Harmonized (MODIS units)</option>
        </select>
      </label>
      <label>Detections
        <select v-model="detMode">
          <option value="side">Each sensor on its side</option>
          <option value="both">Both sensors on both sides</option>
          <option value="none">Hide</option>
        </select>
      </label>
      <span v-if="store.compareHarmonized" class="hint">
        Harmonized: MODIS with bow-tie duplicates merged and size-corrected FRP; VIIRS S-NPP divided by the factor k of each
        hexagon (VIIRS / MODIS, calibrated where both sensors have complete data; elsewhere the regional or global k).
        Both sides are in MODIS units, so they should look alike. Zoomed in, points are still the raw detections.
      </span>
      <span class="hint">
        Drag the handle over a fire front. If the front “jumps” between sides there is an offset;
        if it looks sharper on VIIRS, that is resolution.
      </span>
    </div>

    <div class="stage">
      <div ref="leftEl" class="cmap"></div>
      <div ref="rightEl" class="cmap" :style="{ clipPath: `inset(0 0 0 ${split}%)` }"></div>
      <div class="line" :style="{ left: `${split}%` }"></div>
      <input v-model.number="split" type="range" class="handle" min="0" max="100" step="0.2" aria-label="Curtain position">

      <div class="lab l">◀ MODIS <small>{{ leftNote }}</small></div>
      <div class="lab r">VIIRS ▶ <small>{{ rightNote }}</small></div>

      <div class="status">
        <span v-if="loading" class="spinner" aria-label="Loading"></span>
        <span v-if="error" class="error">{{ error }}</span>
        <template v-else-if="result">
          <span>{{ summary }}</span>
          <span v-if="truncated" class="muted">· showing up to {{ fmt(POINTS_LIMIT) }} per sensor: zoom in to see all</span>
          <span v-if="detMode === 'both' && !zoomedIn" class="muted">· zoom in to see both sensors on each side</span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.compare { display: flex; flex-direction: column; height: 100%; }
.bar {
  display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; padding: 8px 14px;
  background: var(--panel); border-bottom: 1px solid var(--border); font-size: 13px;
}
.bar label { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: 12px; }
.bar select {
  font: inherit; font-size: 12px; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px;
}
.hint { flex-basis: 100%; font-size: 12px; color: var(--muted); }
.stage { position: relative; flex: 1; min-height: 0; }
.cmap { position: absolute; inset: 0; }
.line {
  position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: #fff;
  box-shadow: 0 0 0 1px #000; z-index: 2; pointer-events: none;
}
/* Full-width invisible range: only its thumb takes the mouse, so the maps keep their clicks. */
.handle {
  position: absolute; left: 0; top: 50%; width: 100%; height: 0; margin: 0; z-index: 3;
  appearance: none; -webkit-appearance: none; background: transparent; pointer-events: none;
}
.handle::-webkit-slider-thumb {
  -webkit-appearance: none; pointer-events: auto; width: 26px; height: 48px; border-radius: 8px;
  background: var(--accent); border: 2px solid #fff; cursor: ew-resize; box-shadow: var(--shadow);
}
.handle::-moz-range-thumb {
  pointer-events: auto; width: 26px; height: 48px; border-radius: 8px;
  background: var(--accent); border: 2px solid #fff; cursor: ew-resize; box-shadow: var(--shadow);
}
.lab {
  position: absolute; top: 12px; z-index: 2; padding: 4px 10px; border-radius: 7px;
  font-weight: 700; font-size: 12.5px; pointer-events: none; box-shadow: var(--shadow);
}
.lab small { font-weight: 500; opacity: 0.8; margin-left: 4px; }
.lab.l { left: 12px; background: #ffcc00; color: #1a1400; }
.lab.r { right: 56px; background: #22d3ee; color: #00181d; }
.status {
  position: absolute; bottom: 28px; left: 12px; z-index: 2; max-width: calc(100% - 24px);
  display: flex; gap: 6px; align-items: center; flex-wrap: wrap; background: var(--panel);
  padding: 6px 10px; border-radius: 6px; font-size: 13px; box-shadow: var(--shadow);
}
.status:empty { display: none; }
.muted { color: var(--muted); }
.error { color: var(--danger); }
.spinner {
  width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 2.4s; } }
</style>
