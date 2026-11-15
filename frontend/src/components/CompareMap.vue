<script setup>
// Split view: two synced maps stacked on top of each other. The top (right) map is
// clipped from the handle to the right, so dragging the handle compares both sides.
// Two ways to compare:
//   - Epochs (default): the same place in two periods, months or years, each side the
//     aggregate of its period. Harmonized by default (one series in MODIS units: MODIS
//     before 2012-02, VIIRS S-NPP / k after), or the chosen dataset's raw values. Periods
//     are always inside the chosen dataset's coverage, so it's the same data the rest of
//     the app shows. Always hexagons (aggregates), at any zoom.
//   - MODIS | VIIRS: both sensors over the side panel's whole date range, MODIS on the
//     left, VIIRS on the right: how each sensor sees the same fire. Zoomed in, detections
//     are drawn per sensor: MODIS = yellow ring (~1 km pixel), VIIRS = cyan dot (375 m
//     pixel); static heat sources (refineries, gas flares...) in gray.
// No time slider here: each side is a whole period. Both sides share one color scale
// (computed from both), so a lighter hexagon really has less fire per day.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHexagonEdgeLengthAvg } from 'h3-js'
import { getDetections, getHarmonizedHexagons, getHexagons } from '../api'
import { datasetName, date, errorMessage, notBuilt, pct } from '../format'
import { GIBS_LAYERS, gibsDate, gibsUrl, pickLayer } from '../gibs'
import { cellsOutline } from '../hexGrid'
import {
  EMPTY, HEX_FILL_COLOR, POINTS_LIMIT, POINTS_ZOOM, STATIC_COLOR, STYLE, applyScale, densityScale, HEX_BORDER_WIDTH, hexOpacity,
  fmt, hexPopup, hexRes, labelLayerId, localizeBasemap, mapLocale, maplibregl, pointPopup,
  scaleTicks, viewportBbox, whenIdle, withDensity,
} from '../mapShared'
import { useFiltersStore } from '../stores/filters'
import { trackMapSources, useLoadingBar } from '../stores/loading'
import { usePrefsStore } from '../stores/prefs'
import FireScale from './FireScale.vue'
import MouseCoords from './MouseCoords.vue'

// MODIS | VIIRS imagery per sensor: { id, sat, kind } -> label "<sat> (fire)" / "<sat> true color".
const IMAGERY = {
  modis: [
    { id: 'MODIS_Aqua_CorrectedReflectance_Bands721', sat: 'MODIS Aqua 7-2-1', kind: 'fire' },
    { id: 'MODIS_Aqua_CorrectedReflectance_TrueColor', sat: 'MODIS Aqua', kind: 'trueColor' },
    { id: 'MODIS_Terra_CorrectedReflectance_Bands721', sat: 'MODIS Terra 7-2-1', kind: 'fire' },
    { id: 'MODIS_Terra_CorrectedReflectance_TrueColor', sat: 'MODIS Terra', kind: 'trueColor' },
    { id: 'none', kind: 'mapOnly' },
  ],
  viirs: [
    { id: 'VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1', sat: 'VIIRS NOAA-20 M11-I2-I1', kind: 'fire' },
    { id: 'VIIRS_NOAA20_CorrectedReflectance_TrueColor', sat: 'VIIRS NOAA-20', kind: 'trueColor' },
    { id: 'VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1', sat: 'VIIRS S-NPP M11-I2-I1', kind: 'fire' },
    { id: 'VIIRS_SNPP_CorrectedReflectance_TrueColor', sat: 'VIIRS S-NPP', kind: 'trueColor' },
    { id: 'none', kind: 'mapOnly' },
  ],
}
const SENSORS = { modis: 'MODIS', viirs: 'VIIRS' }
const MODIS_COLOR = '#ffcc00'
const VIIRS_COLOR = '#22d3ee'
const SIDES = ['left', 'right']
const DAY_MS = 86_400_000
const daysOf = (start, end) => Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / DAY_MS) + 1)
const pad = (n) => String(n).padStart(2, '0')

const store = useFiltersStore()
const prefs = usePrefsStore()
const { t } = useI18n()
const imageryLabel = (o) => t(`curtain.${o.kind}`, { sat: o.sat })
const leftEl = ref(null)
const rightEl = ref(null)
const stageEl = ref(null)
const split = ref(50) // curtain position, % of the width
const loading = ref(false)
useLoadingBar('curtain', loading) // the top loading bar, MODIS yellow -> VIIRS cyan
const untrack = []
const error = ref(null)
const zoomedIn = ref(false)
const result = ref(null) // last fetch: { kind: 'hex' | 'points', left, right }
const ticks = ref(null) // legend of the shared color scale
const shownMaps = shallowRef([]) // both maps once loaded, for the mouse coordinates

// ---------------------------------------------------------------- modes ---

const mode = ref('epochs') // 'epochs' | 'sensors'
const epochs = computed(() => mode.value === 'epochs')

// MODIS | VIIRS: imagery per side, detections mode.
const img = ref({ left: IMAGERY.modis[0].id, right: IMAGERY.viirs[0].id })
const detMode = ref('side') // 'side' | 'both' | 'none'
const product = computed(() => store.compareSides.product)

// Epochs: month or year, one period per side, harmonized or raw values, optional imagery.
const grain = ref('year') // 'month' | 'year'
const pick = ref({ left: null, right: null }) // period ids: 'YYYY' or 'YYYY-MM'
const epochValues = ref('harmonized') // 'harmonized' | 'raw' (the chosen dataset)
const epochImagery = ref('none') // 'none' | 'truecolor' | 'fire': the last day of each period

// The chosen dataset's coverage: periods never go outside it.
const coverage = computed(() => {
  const d = store.currentDataset
  return d?.available && d.start_date && d.end_date ? { start: d.start_date, end: d.end_date } : null
})
// Every month or year of the coverage, cut to it (then marked partial).
const periods = computed(() => {
  const cov = coverage.value
  if (!cov) return []
  const cut = (id, s, e) => {
    const start = s < cov.start ? cov.start : s
    const end = e > cov.end ? cov.end : e
    return { id, start, end, partial: start !== s || end !== e }
  }
  const [y0, m0] = cov.start.split('-').map(Number)
  const [y1, m1] = cov.end.split('-').map(Number)
  const out = []
  if (grain.value === 'year') {
    for (let y = y0; y <= y1; y++) out.push(cut(String(y), `${y}-01-01`, `${y}-12-31`))
  } else {
    for (let y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m === 12 ? (m = 1, y++) : m++) {
      const last = new Date(Date.UTC(y, m, 0)).getUTCDate()
      out.push(cut(`${y}-${pad(m)}`, `${y}-${pad(m)}-01`, `${y}-${pad(m)}-${pad(last)}`))
    }
  }
  return out
})
const periodName = (p) => (grain.value === 'year' ? p.id : date(`${p.id}-01`, 'monthYear'))
const periodOption = (p) => (p.partial ? t('curtain.epochs.partial', { period: periodName(p) }) : periodName(p))

// Defaults: the latest period on the right; on the left the same month a year before
// (months) or the period before. Picks that are still valid are kept.
function placePicks(force = false) {
  const list = periods.value
  if (!list.length) return
  const has = (id) => list.some((p) => p.id === id)
  if (!force && has(pick.value.left) && has(pick.value.right)) return
  const right = list.at(-1)
  const yearBefore = grain.value === 'month' ? list.find((p) => p.id === `${Number(right.id.slice(0, 4)) - 1}${right.id.slice(4)}`) : null
  pick.value = { left: (yearBefore ?? list.at(-2) ?? right).id, right: right.id }
}
function setGrain(g) {
  if (g === grain.value) return
  grain.value = g
  placePicks(true)
}
// A new dataset (or its coverage): years when it spans two or more, else months.
watch(coverage, (cov, old) => {
  if (!cov) return
  if (!old) grain.value = Number(cov.end.slice(0, 4)) > Number(cov.start.slice(0, 4)) ? 'year' : 'month'
  placePicks()
}, { immediate: true })

const datasetFor = (sensor) => store.compareSides[sensor === 'modis' ? 'left' : 'right']

// What each side shows: its filters, its imagery day and its label.
function sideConfig(side) {
  if (epochs.value) {
    const p = periods.value.find((x) => x.id === pick.value[side])
    return {
      period: p,
      name: p ? periodName(p) : '–',
      params: p ? { start: p.start, end: p.end, zone: store.zone } : {},
      imageryDay: p?.end,
    }
  }
  const sensor = side === 'left' ? 'modis' : 'viirs'
  return {
    sensor,
    ds: datasetFor(sensor),
    name: SENSORS[sensor],
    params: { start: store.start, end: store.end, zone: store.zone }, // the panel's whole range
    imageryDay: store.end,
  }
}
const config = computed(() => ({ left: sideConfig('left'), right: sideConfig('right') }))

// A side's imagery list follows its sensor; keep the choice if it's still valid.
watch(() => [config.value.left.sensor, config.value.right.sensor], ([l, r]) => {
  for (const [side, sensor] of [['left', l], ['right', r]]) {
    if (sensor && !IMAGERY[sensor].some((o) => o.id === img.value[side])) img.value = { ...img.value, [side]: IMAGERY[sensor][0].id }
  }
}, { immediate: true })

// ---------------------------------------------------------------- maps ---

const maps = { left: null, right: null }
let loaded = 0
let syncing = false
let controller = null
let debounce = null
let resizer = null

function createMap(el, side) {
  const map = new maplibregl.Map({
    container: el,
    style: STYLE,
    center: store.camera.center,
    zoom: store.camera.zoom,
    renderWorldCopies: false,
    attributionControl: side === 'right' ? { compact: true } : false,
    locale: { ...mapLocale(), 'Map.Title': side === 'left' ? t('curtain.mapLeft') : t('curtain.mapRight') },
  })
  if (side === 'right') map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => {
    untrack.push(trackMapSources(map, { img: 'imagery', openmaptiles: 'basemap' }))
    addLayers(map)
    localizeBasemap(map, prefs.locale)
    applyContrast(map)
    if (++loaded === 2) onBothLoaded()
  })
  return map
}

function addLayers(map) {
  const labels = labelLayerId(map)
  map.addSource('img', { type: 'raster', tileSize: 256, maxzoom: 9, tiles: [gibsUrl(GIBS_LAYERS[0].id, '2025-01-01')],
    attribution: 'Imagery: <a href="https://www.earthdata.nasa.gov/gibs">EOSDIS GIBS</a>' })
  map.addLayer({ id: 'img', type: 'raster', source: 'img', layout: { visibility: 'none' },
    paint: { 'raster-brightness-max': 0.8 } }, labels)
  for (const id of ['hex', 'modis', 'viirs', 'zone']) map.addSource(id, { type: 'geojson', data: EMPTY })
  map.addLayer({ id: 'hex-fill', type: 'fill', source: 'hex',
    paint: { 'fill-color': HEX_FILL_COLOR, 'fill-opacity': hexOpacity(0.8) } }, labels)
  map.addLayer({ id: 'hex-line', type: 'line', source: 'hex',
    paint: { 'line-color': '#7c2d12', 'line-width': HEX_BORDER_WIDTH, 'line-opacity': 0.2 } }, labels)
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
  shownMaps.value = [maps.left, maps.right]
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
  // Epochs are aggregates: always hexagons. MODIS | VIIRS: detections once zoomed in.
  zoomedIn.value = !epochs.value && zoom >= POINTS_ZOOM
  const res = hexRes(zoom)
  const bbox = viewportBbox(maps.left)
  const hexBbox = viewportBbox(maps.left, 2 * getHexagonEdgeLengthAvg(res, 'km')) // cells partly on screen too
  const fetchSide = (c) => {
    const { params } = c
    if (!params.start || !params.end) return Promise.resolve(null)
    if (epochs.value) {
      const p = { ...params, bbox: hexBbox, res }
      if (epochValues.value === 'harmonized') return getHarmonizedHexagons(p, signal)
      return store.currentDataset?.available ? getHexagons(store.dataset, p, signal) : Promise.resolve(null)
    }
    if (!c.ds.available) return Promise.resolve(null)
    return zoomedIn.value
      ? getDetections(c.ds.id, { ...params, bbox, limit: POINTS_LIMIT }, signal)
      : getHexagons(c.ds.id, { ...params, bbox: hexBbox, res }, signal)
  }
  loading.value = true
  store.setBusy('curtain', true)
  try {
    const sides = config.value
    const [left, right] = await Promise.all([fetchSide(sides.left), fetchSide(sides.right)])
    if (!zoomedIn.value) {
      // Fire per day of each side's own period, then ONE scale for both sides.
      for (const [r, c] of [[left, sides.left], [right, sides.right]]) withDensity(r, c.params.start, c.params.end)
      const stops = densityScale([left, right].flatMap((r) => (r?.features ?? []).map((f) => f.properties.density)))
      applyScale(left, stops)
      applyScale(right, stops)
      ticks.value = scaleTicks(stops)
    } else ticks.value = null
    result.value = { kind: zoomedIn.value ? 'points' : 'hex', left, right }
    error.value = null
    render()
    await Promise.all([whenIdle(maps.left), whenIdle(maps.right)])
    if (!signal.aborted) store.setBusy('curtain', false)
  } catch (e) {
    if (e.name === 'AbortError') return // a newer refresh owns the busy flag
    error.value = e
    result.value = null
    for (const m of [maps.left, maps.right]) for (const id of ['hex', 'modis', 'viirs']) m.getSource(id).setData(EMPTY)
    store.setBusy('curtain', false)
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

// Put the last fetch on each side according to the detections mode (no refetch).
// Points go to the layer of their sensor (MODIS ring, VIIRS dot), whatever the side.
function render() {
  if (!bothReady() || !result.value) return
  const { kind } = result.value
  const fc = (r) => r ?? EMPTY
  const hide = !epochs.value && detMode.value === 'none'
  const both = !epochs.value && detMode.value === 'both'
  for (const side of SIDES) {
    const map = maps[side]
    const other = side === 'left' ? 'right' : 'left'
    if (kind === 'hex') {
      // Hexagons of two sensors or periods can't be overlaid: each side shows its own.
      map.getSource('hex').setData(hide ? EMPTY : fc(result.value[side]))
      map.getSource('modis').setData(EMPTY)
      map.getSource('viirs').setData(EMPTY)
    } else {
      const points = { modis: EMPTY, viirs: EMPTY }
      if (!hide) {
        if (both) points[config.value[other].sensor] = fc(result.value[other])
        points[config.value[side].sensor] = fc(result.value[side])
      }
      map.getSource('hex').setData(EMPTY)
      map.getSource('modis').setData(points.modis)
      map.getSource('viirs').setData(points.viirs)
    }
  }
}

const total = (r) => {
  if (!r) return null
  return result.value?.kind === 'hex' ? r.features.reduce((s, f) => s + f.properties.detections, 0) : r.features.length
}
const signed = (share) => `${share >= 0 ? '+' : '−'}${pct(Math.abs(share))}`
const summary = computed(() => {
  const left = total(result.value?.left)
  const right = total(result.value?.right)
  const value = (n) => (n === null ? t('common.noData') : fmt(n))
  if (epochs.value) {
    const { left: l, right: r } = config.value
    const units = epochValues.value === 'harmonized' ? t('harm.modisUnitsSuffix') : ''
    let change = ''
    if (left > 0 && right !== null && l.period && r.period) {
      // Per day, so a partial year or a February compares fairly; equal lengths: same as the totals.
      const perDay = (n, p) => n / daysOf(p.start, p.end)
      const sameLength = daysOf(l.period.start, l.period.end) === daysOf(r.period.start, r.period.end)
      const values = { pct: signed(perDay(right, r.period) / perDay(left, l.period) - 1) }
      change = sameLength ? t('curtain.epochs.change', values) : t('curtain.epochs.changePerDay', values)
    }
    return t('curtain.epochs.summary', { left: l.name, leftN: value(left), right: r.name, rightN: value(right) }) + units + change
  }
  const ratio = left && right ? t('curtain.ratio', { r: fmt(right / left) }) : ''
  const units = store.compareHarmonized && result.value?.kind === 'hex' ? t('harm.modisUnitsSuffix') : ''
  return t('curtain.summary', { modis: value(left), viirs: value(right) }) + ratio + units
})
const truncated = computed(() => result.value?.kind === 'points' && (result.value.left?.truncated || result.value.right?.truncated))
// The harmonized layer isn't built yet: say so and offer the raw values.
const harmonizedMissing = computed(() => epochs.value && epochValues.value === 'harmonized' && notBuilt(error.value))

// ------------------------------------------------------------- imagery ---

function imageryState(side) {
  const wanted = config.value[side].imageryDay
  if (epochs.value) {
    // The satellite that has imagery for that day (MODIS before VIIRS existed).
    if (epochImagery.value === 'none' || !wanted) return { tiles: null, note: '' }
    const picked = pickLayer(epochImagery.value, wanted)
    return picked ? { tiles: gibsUrl(picked.layer.id, picked.date), note: date(picked.date) } : { tiles: null, note: t('curtain.noImagery') }
  }
  const id = img.value[side]
  if (id === 'none') return { tiles: null, note: t('curtain.mapOnlyNote') }
  const layer = GIBS_LAYERS.find((l) => l.id === id)
  const day = layer && gibsDate(layer, wanted)
  return day ? { tiles: gibsUrl(id, day), note: date(day) } : { tiles: null, note: t('curtain.noImagery') }
}
const notes = computed(() => ({ left: imageryState('left').note, right: imageryState('right').note }))
// Under the label of each side: the period's days (epochs) or the imagery day.
const sideNote = (side) => {
  const c = config.value[side]
  if (!epochs.value || !c.period) return notes.value[side]
  const days = `${date(c.period.start)} – ${date(c.period.end)}`
  return notes.value[side] ? t('curtain.epochs.imageryOf', { days, image: notes.value[side] }) : days
}

function syncImagery() {
  if (!bothReady()) return
  for (const side of SIDES) {
    const { tiles } = imageryState(side)
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

// High contrast (accessibility menu): stronger hexagon borders and fill. Normal borders
// are a whole pixel, light (mapShared.js HEX_BORDER_WIDTH), so they look alike on every screen.
function applyContrast(map) {
  map.setPaintProperty('hex-line', 'line-opacity', prefs.contrast ? 0.9 : 0.2)
  map.setPaintProperty('hex-line', 'line-width', prefs.contrast ? 1.2 : HEX_BORDER_WIDTH)
  map.setPaintProperty('hex-fill', 'fill-opacity', hexOpacity(prefs.contrast ? 0.95 : 0.8))
}
const applyContrastBoth = () => { if (bothReady()) for (const m of [maps.left, maps.right]) applyContrast(m) }

// ------------------------------------------------------------ lifecycle ---

onMounted(() => {
  maps.left = createMap(leftEl.value, 'left')
  maps.right = createMap(rightEl.value, 'right')
  // The side panel folding or unfolding changes the width.
  resizer = new ResizeObserver(() => { if (stageEl.value?.clientWidth) for (const m of [maps.left, maps.right]) m?.resize() })
  resizer.observe(stageEl.value)
})

onBeforeUnmount(() => {
  controller?.abort()
  clearTimeout(debounce)
  resizer?.disconnect()
  store.setBusy('curtain', false)
  maps.left?.remove()
  maps.right?.remove()
  for (const stop of untrack) stop()
})

// Refetch only when what a side asks for changes.
watch(() => JSON.stringify([mode.value, epochValues.value, store.dataset, SIDES.map((s) => [config.value[s].ds?.id, config.value[s].params])]), refresh)
watch(detMode, render)
watch(() => [mode.value, epochImagery.value, img.value.left, img.value.right, config.value.left.imageryDay, config.value.right.imageryDay], syncImagery)
watch(() => [store.zone, store.zoneCells], syncZone)
watch(() => prefs.locale, (locale) => { if (bothReady()) for (const m of [maps.left, maps.right]) localizeBasemap(m, locale) })
watch(() => prefs.contrast, applyContrastBoth)
</script>

<template>
  <div class="compare">
    <div class="bar">
      <b>{{ epochs ? $t('curtain.epochs.title') : $t('curtain.title', { product: product.toUpperCase() }) }}</b>
      <div class="toggle" role="group" :aria-label="$t('curtain.compare')">
        <button type="button" :aria-pressed="epochs" :class="{ on: epochs }" @click="mode = 'epochs'">{{ $t('curtain.epochs.mode') }}</button>
        <button type="button" :aria-pressed="!epochs" :class="{ on: !epochs }" @click="mode = 'sensors'">{{ $t('curtain.sensorsMode') }}</button>
      </div>

      <template v-if="epochs">
        <div class="toggle" role="group" :aria-label="$t('curtain.epochs.grain')">
          <button type="button" :aria-pressed="grain === 'month'" :class="{ on: grain === 'month' }" @click="setGrain('month')">
            {{ $t('curtain.epochs.month') }}
          </button>
          <button type="button" :aria-pressed="grain === 'year'" :class="{ on: grain === 'year' }" @click="setGrain('year')">
            {{ $t('curtain.epochs.year') }}
          </button>
        </div>
        <label>{{ $t('curtain.left') }}
          <select v-model="pick.left" :disabled="!periods.length">
            <option v-for="p in periods" :key="p.id" :value="p.id">{{ periodOption(p) }}</option>
          </select>
        </label>
        <label>{{ $t('curtain.right') }}
          <select v-model="pick.right" :disabled="!periods.length">
            <option v-for="p in periods" :key="p.id" :value="p.id">{{ periodOption(p) }}</option>
          </select>
        </label>
        <div class="toggle" role="group" :aria-label="$t('harm.values')">
          <button type="button" :aria-pressed="epochValues === 'harmonized'" :class="{ on: epochValues === 'harmonized' }" @click="epochValues = 'harmonized'">
            {{ $t('harm.harmonized') }}
          </button>
          <button type="button" :aria-pressed="epochValues === 'raw'" :class="{ on: epochValues === 'raw' }" @click="epochValues = 'raw'">
            {{ $t('harm.raw') }}
          </button>
        </div>
        <label>{{ $t('curtain.imagery') }}
          <select v-model="epochImagery">
            <option value="none">{{ $t('curtain.mapOnly') }}</option>
            <option value="truecolor">{{ $t('gibs.truecolor') }}</option>
            <option value="fire">{{ $t('gibs.fire') }}</option>
          </select>
        </label>
        <span class="hint">
          {{ $t('curtain.epochs.hint', { dataset: datasetName(store.currentDataset) }) }}
          {{ epochValues === 'harmonized' ? $t('curtain.epochs.harmonizedHint') : '' }}
        </span>
      </template>

      <template v-else>
        <label>{{ $t('curtain.left') }}
          <select v-model="img.left">
            <option v-for="o in IMAGERY.modis" :key="o.id" :value="o.id">{{ imageryLabel(o) }}</option>
          </select>
        </label>
        <label>{{ $t('curtain.right') }}
          <select v-model="img.right">
            <option v-for="o in IMAGERY.viirs" :key="o.id" :value="o.id">{{ imageryLabel(o) }}</option>
          </select>
        </label>
        <label>{{ $t('harm.values') }}
          <select v-model="store.compareHarmonized">
            <option :value="false">{{ $t('harm.raw') }}</option>
            <option :value="true">{{ $t('harm.harmonizedUnits') }}</option>
          </select>
        </label>
        <label>{{ $t('curtain.detections') }}
          <select v-model="detMode">
            <option value="side">{{ $t('curtain.modeSide') }}</option>
            <option value="both">{{ $t('curtain.modeBoth') }}</option>
            <option value="none">{{ $t('curtain.modeNone') }}</option>
          </select>
        </label>
        <span v-if="store.compareHarmonized" class="hint">{{ $t('harm.curtainHint') }}</span>
        <span class="hint">{{ $t('curtain.hint') }} {{ $t('curtain.rangeHint', { start: date(store.start), end: date(store.end) }) }}</span>
      </template>
    </div>

    <div ref="stageEl" class="stage">
      <div ref="leftEl" class="cmap"></div>
      <div ref="rightEl" class="cmap" :style="{ clipPath: `inset(0 0 0 ${split}%)` }"></div>
      <div class="line" :style="{ left: `${split}%` }"></div>
      <input
        v-model.number="split" type="range" class="handle" min="0" max="100" step="0.2"
        :aria-label="$t('curtain.handle')" :aria-valuetext="`${Math.round(split)} %`"
      >

      <div v-for="side in SIDES" :key="side" class="lab" :class="[side === 'left' ? 'l' : 'r', epochs ? 'period' : config[side].sensor]">
        <span v-if="side === 'left'" aria-hidden="true">◀ </span>{{ config[side].name }}<span v-if="side === 'right'" aria-hidden="true"> ▶</span>
        <small>{{ sideNote(side) }}</small>
      </div>

      <div class="status" aria-live="polite">
        <span v-if="loading" class="spinner" role="img" :aria-label="$t('common.loading')"></span>
        <template v-if="harmonizedMissing">
          <span class="error">{{ $t('harm.notBuilt') }}</span>
          <button type="button" class="link" @click="epochValues = 'raw'">{{ $t('curtain.epochs.useRaw') }}</button>
        </template>
        <span v-else-if="error" class="error">{{ errorMessage(error) }}</span>
        <template v-else-if="result">
          <span>{{ summary }}</span>
          <span v-if="truncated" class="muted">{{ $t('curtain.truncated', { n: fmt(POINTS_LIMIT) }) }}</span>
          <span v-if="detMode === 'both' && !epochs && !zoomedIn" class="muted">{{ $t('curtain.bothHint') }}</span>
          <FireScale v-if="ticks" :ticks="ticks" />
        </template>
        <span v-else-if="epochs && !periods.length" class="muted">{{ $t('curtain.epochs.noCoverage') }}</span>
      </div>
      <MouseCoords :maps="shownMaps" />
    </div>
  </div>
</template>

<style scoped>
.compare { display: flex; flex-direction: column; height: 100%; }
.bar {
  display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; padding: 8px 14px;
  background: var(--panel); border-bottom: 1px solid var(--border); font-size: 0.8125rem;
}
.bar label { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: 0.75rem; }
.bar select {
  font: inherit; font-size: 0.75rem; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px;
}
.toggle { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.toggle button {
  border: none; background: var(--panel); color: var(--text); padding: 4px 9px; cursor: pointer;
  font: inherit; font-size: 0.75rem;
}
.toggle button.on { background: var(--accent); color: var(--on-accent); }
.hint { flex-basis: 100%; font-size: 0.75rem; color: var(--muted); }
.stage { position: relative; flex: 1; min-height: 0; }
.cmap { position: absolute; inset: 0; }
.line {
  position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: #ffffff;
  box-shadow: 0 0 0 1px #000000; z-index: 2; pointer-events: none;
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
  font-weight: 700; font-size: 0.7812rem; pointer-events: none; box-shadow: var(--shadow);
}
.lab small { display: block; font-weight: 500; opacity: 0.85; }
.lab.l { left: 12px; }
.lab.r { right: 56px; text-align: right; }
/* Sensors: each label in its sensor's color. Periods: neutral. */
.lab.modis { background: #ffcc00; color: #1a1400; }
.lab.viirs { background: #22d3ee; color: #00181d; }
.lab.period { background: var(--panel); color: var(--text); border: 1px solid var(--border); }
.status {
  position: absolute; bottom: 28px; left: 12px; z-index: 2; max-width: calc(100% - 24px);
  display: flex; gap: 6px; align-items: center; flex-wrap: wrap; background: var(--panel);
  padding: 6px 10px; border-radius: 6px; font-size: 0.8125rem; box-shadow: var(--shadow);
}
.status:empty { display: none; }
.link {
  font: inherit; font-size: 0.75rem; background: none; color: var(--accent); border: 1px solid var(--accent);
  border-radius: 5px; padding: 2px 6px; cursor: pointer;
}
.muted { color: var(--muted); }
.error { color: var(--danger); }
.spinner {
  width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 2.4s; } }
</style>
