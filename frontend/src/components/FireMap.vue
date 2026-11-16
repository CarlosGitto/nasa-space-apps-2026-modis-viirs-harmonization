<script setup>
// Map of H3 hexagons: coarse parents when zoomed out, the fixed res-6 grid from
// BASE_ZOOM on. Hexagons never go away: from DETAIL_ZOOM their border turns dashed
// and individual detections are drawn on top. Click a coarse hexagon to zoom into
// it; click a res-6 hexagon to open it in the panel (Shift/Ctrl/Cmd adds more).
// The whole grid is always drawn (borders only, built in the browser, see hexGrid.js);
// only hexagons with detections are filled and clickable.
// Always the whole world unless the user draws an area: 4 clicks, then every res-6
// hexagon whose center is inside it is selected (empty ones too) and the map is filtered.
// Optional NASA GIBS satellite imagery under the data, for the end day of the selected range.
// Optional weather layers (temperature, rain, wind arrows) for the same day, under the
// hexagons; see mapWeather.js.
//
// 3D: the fire hexagons rise as columns, height = the same level as their color, and the map tilts.
//
// Keyboard: with the map focused, arrows move it and +/− zoom (MapLibre); Enter does what a
// click at the center (the crosshair) does, Shift+Enter adds or removes that hexagon.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  getDetections, getHarmonizedHexagons, getHarmonizedHexagonsTypical, getHexagons, getHexagonsTypical, getSelectionWeather, getWeatherMap,
} from '../api'
import { date, errorMessage } from '../format'
import { gibsUrl } from '../gibs'
import {
  MAX_SELECTION, areaPolygon, cellPolygon, cellsInArea, cellsOutline, covers, gridArea, gridCells,
} from '../hexGrid'
import { getHexagonEdgeLengthAvg } from 'h3-js'
import {
  BASE_RES, DETAIL_ZOOM, EMPTY, HEX_ANOMALY_COLOR, HEX_FILL_COLOR, POINTS_LIMIT, STATIC_COLOR, STYLE,
  anomalyOpacity, applyScale, densityScale, withAnomaly, fitRegion, fmt, HEX_BORDER_WIDTH, hexOpacity, hexPopup, hexRes, labelLayerId, localizeBasemap, mapLocale, maplibregl, pointPopup,
  scaleTicks, viewportBbox, whenIdle, withDensity,
} from '../mapShared'
import FireScale from './FireScale.vue'
import MouseCoords from './MouseCoords.vue'
import { contourContrast, setContours } from '../relief'
import { cellToLatLng, gridDiskDistances, latLngToCell } from 'h3-js'
import {
  RAIN_SIZE, TEMPERATURE_SIZE, WEATHER_IDS, WEATHER_LAYERS, WIND_SIZE, colorAt, mergedGrids, selectionDay, selectionGrids, colorRamp, fireCandidates, fireNeighborhood, gridCandidates,
  iconImage, rainSize, responseCache, weatherGrid, weatherPoints, weatherRequest, weatherTooltip, windSize,
} from '../mapWeather'
import { useFiltersStore } from '../stores/filters'
import { trackMapSources, useLoadingBar } from '../stores/loading'
import { usePrefsStore } from '../stores/prefs'

const store = useFiltersStore()
const prefs = usePrefsStore()
const threeD = ref(false)
const LEVEL_3D = ['max', 0.04, ['coalesce', ['get', 'level'], 0]] // a little column even for the faintest hexagon
function toggle3d() {
  threeD.value = !threeD.value
  if (!mapReady) return
  map.setLayoutProperty('hex-3d', 'visibility', threeD.value ? 'visible' : 'none')
  map.easeTo({ pitch: threeD.value ? 55 : 0, bearing: threeD.value ? -15 : 0, duration: prefs.reduceMotion ? 0 : 900, essential: true })
}
const { t } = useI18n()
const container = ref(null)
const AREA_POINTS = 4
const tool = ref(null) // 'area' while drawing an area, else null
const draft = ref([]) // [lng, lat] of the points clicked so far
const areaNote = ref(null) // { n } hexagons selected, or { tooBig: true }
const loading = ref(false)
const scheduled = ref(false) // the map stopped and a refresh is on its way (the 250 ms wait)
// The top loading bar, in fire orange: from the moment the map stops until the new
// hexagons are drawn, so it never ends before they change.
useLoadingBar('hexagons', computed(() => scheduled.value || loading.value))
const weatherLoading = ref(false)
useLoadingBar('weather', weatherLoading) // the weather layers and the selection's weather
let untrackSources = null
const error = ref(null) // the API error (with .status), shown in the user's language
const info = ref(null) // what's on screen: { total, hexagons, res, truncated }
const fireTicks = ref(null) // legend of the color scale: [low, middle, high] densities
const noYears = ref(false) // anomalies with no other year to compare with (the legend says so)
// Typical hexagons (anomalies) per range, area, view and resolution: asked once, kept while the slider plays.
const typicals = new Map()
let paintedMeasure = null // the measure the hexagons' paint is set for
const mapRef = shallowRef(null) // the MapLibre map, for the mouse coordinates (never made deeply reactive)
// Color scale per range (dataset, dates, area, resolution): computed from the whole range's hexagons and kept, so
// while the time slider plays a day is colored against its range and the colors don't jump from day to day.
const scales = new Map()
const daysOf = (start, end) => Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / 86_400_000) + 1)
const noImagery = ref(false) // the chosen imagery has no picture for that date
const weatherNotes = ref({}) // layer id -> { first, last } when that day has no data for it

let map = null
let mapReady = false // set on 'load'; isStyleLoaded() flips to false while sources update
let controller = null
let weatherToken = 0 // increases with every refreshWeather call
let debounce = null
let resizer = null
let tooltip = null
let currentRes = null // resolution of the hexagons on screen
let hexById = new Map() // h3 -> feature on screen (whole polygon, not the tiled one MapLibre returns)
let gridShown = null // area (and resolution) of the grid on screen
let draftFrame = null // pending preview redraw (one per animation frame)
const weatherCache = responseCache() // GET /weather/map responses, by layer, day and bbox
let weatherGrids = {} // layer id -> grid of the day shown (see weatherGrid)
let neighborhood = new Map() // h3 -> ring distance to a fire hexagon on screen: where weather is drawn
// Weather of the selected hexagons, asked on demand (NASA POWER) for the whole date range,
// so the slider animates it without more requests. Shown even with no weather layer on.
const selectionWeather = ref(null)
const selectionNote = ref('')
let selectionToken = 0
let selectionTimer = null
const SELECTION_MAX_DAYS = 366
const SELECTION_RINGS = 3 // neighbors around the selected hexagons that get weather too
const SELECTION_RINGS_MAX_HEXAGONS = 300 // a bigger selection (a drawn area) covers itself
let selectionArea = new Map() // h3 -> ring distance to a selected hexagon (0 = selected)
// The layers drawn: the ones turned on, or all three when only a selection has weather.
const shownLayers = computed(() =>
  store.weatherLayers.length ? store.weatherLayers : selectionWeather.value ? WEATHER_IDS : [])
// The selection's day drawn: the map's day, or the latest before it POWER has published.
const selectionShownDay = computed(() => selectionDay(selectionWeather.value, store.imageryDate))

// Smallest zoom showing a finer grid than `res`: clicking a coarse hexagon always goes down a level.
const NEXT_LEVEL_ZOOM = { 0: 3, 1: 3, 2: 3, 3: 4.5, 4: 6, 5: 7.5 }

// Hexagons of the calendar's values: the harmonized series (MODIS units, 2000 onward) or the chosen dataset.
const hexagons = (params, signal) =>
  store.harmonized ? getHarmonizedHexagons(params, signal) : getHexagons(store.dataset, params, signal)
// What those hexagons usually have on the same dates of the other years (anomalies).
const hexagonsTypical = (params, signal) =>
  store.harmonized ? getHarmonizedHexagonsTypical(params, signal) : getHexagonsTypical(store.dataset, params, signal)
// Up close the points are always original detections, from the chosen dataset when it covers the dates.
const pointsCovered = () => {
  const d = store.currentDataset
  return d?.available && d.start_date <= store.queryParams.end && d.end_date >= store.queryParams.start
}

async function refresh() {
  // Hidden while the MODIS | VIIRS curtain is shown: don't fetch for nothing.
  if (!mapReady || store.view !== 'map' || !store.ready) return
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  const zoom = map.getZoom()
  const params = { ...store.queryParams, bbox: viewportBbox(map) }
  const res = hexRes(zoom)
  const hexBbox = viewportBbox(map, 2 * getHexagonEdgeLengthAvg(res, 'km')) // cells partly on screen too
  loading.value = true
  store.setBusy('map', true)
  try {
    // Anomalies: what these hexagons usually have on the same dates of the other years, for the whole range (kept
    // while the slider plays: a day is compared with its share of it).
    const anomaly = store.measure === 'anomaly'
    const typicalKey = JSON.stringify([store.values, store.dataset, store.start, store.end, store.zone, res, hexBbox])
    const [hexes, points, typical] = await Promise.all([
      hexagons({ ...params, bbox: hexBbox, res }, signal),
      zoom >= DETAIL_ZOOM && pointsCovered() ? getDetections(store.dataset, { ...params, limit: POINTS_LIMIT }, signal) : null,
      anomaly ? typicals.get(typicalKey) ?? hexagonsTypical({ start: store.start, end: store.end, zone: store.zone, bbox: hexBbox, res }, signal) : null,
    ])
    currentRes = res
    let fc = hexes
    // Density of what the calendar colors by: detections, or FRP (MW) for fire power.
    const field = store.measure === 'frp' ? 'frp_sum' : 'detections'
    withDensity(fc, store.queryParams.start, store.queryParams.end, field)
    if (anomaly) {
      typicals.set(typicalKey, typical)
      if (typicals.size > 30) typicals.delete(typicals.keys().next().value)
      fc = withAnomaly(fc, typical, daysOf(store.queryParams.start, store.queryParams.end) / daysOf(store.start, store.end))
      noYears.value = !typical.years
      fireTicks.value = [0.25, 1, 4]
    } else {
      noYears.value = false
      // The scale comes from the whole range. Without a day on the slider, that's this answer;
      // with one, the range's scale kept for this view (or asked once if there is none yet).
      const key = JSON.stringify([store.values, store.dataset, store.measure, store.start, store.end, store.zone, res])
      if (!store.day) scales.set(key, densityScale(fc.features.map((f) => f.properties.density)))
      else if (!scales.has(key)) {
        const range = await hexagons({ start: store.start, end: store.end, zone: store.zone, bbox: hexBbox, res }, signal)
        withDensity(range, store.start, store.end, field)
        scales.set(key, densityScale(range.features.map((f) => f.properties.density)))
      }
      if (scales.size > 50) scales.delete(scales.keys().next().value)
      // A day against its range: the range's densities are per day of the whole range, a day's are per that one day,
      // so compare the day's against the range's totals (density x days of the range).
      const factor = store.day ? 1 / daysOf(store.start, store.end) : 1
      applyScale(fc, scales.get(key), factor)
      fireTicks.value = scaleTicks(scales.get(key), factor)
    }
    hexById = new Map(fc.features.map((f) => [f.properties.h3, f]))
    map.getSource('hex').setData(fc)
    if (paintedMeasure !== store.measure) { paintedMeasure = store.measure; applyContrast() } // colors with their values
    renderWeather() // the halo follows the fire hexagons on screen
    map.getSource('points').setData(points ?? EMPTY)
    info.value = {
      total: fc.features.reduce((s, f) => s + f.properties.detections, 0),
      hexagons: fc.features.filter((f) => f.properties.detections > 0).length, // not the usual-but-quiet ones (anomalies)
      res,
      truncated: Boolean(points?.truncated),
    }
    error.value = null
    await whenIdle(map) // drawn (data and imagery) before the time slider moves on
    if (!signal.aborted) store.setBusy('map', false)
  } catch (e) {
    if (e.name === 'AbortError') return // a newer refresh owns the busy flag
    error.value = e
    store.setBusy('map', false)
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

// Weather layers for the map's day (the slider's, else the end of the range). Responses
// are cached: temperature and wind come for the whole area (one request per day), rain
// (NASA POWER near fires, ingested by the ETL). A day without data for a layer
// leaves a note saying which days it has.
// The cache holds promises, not responses: a request already on its way (another layer
// toggled, the prefetch, a quick pan) is shared instead of cancelled and sent again.
// Responses are small; a stale one is just ignored by refreshWeather.
function loadWeather(id, date) {
  const request = weatherRequest(id, date)
  const key = `${id}|${request.date}|${request.bbox ?? ''}`
  let pending = weatherCache.get(key)
  if (!pending) {
    pending = getWeatherMap(id, request).catch((e) => { weatherCache.delete(key); throw e })
    weatherCache.set(key, pending)
  }
  return pending
}

async function refreshWeather() {
  if (!mapReady || store.view !== 'map') return
  const token = ++weatherToken // only the newest call draws
  const on = store.weatherLayers
  if (!on.length) {
    weatherGrids = {}
    weatherNotes.value = {}
    weatherLoading.value = false
    renderWeather()
    return
  }
  store.setBusy('weather', true)
  weatherLoading.value = true
  try {
    const day = store.imageryDate
    const results = await Promise.all(on.map((id) => loadWeather(id, day).then((r) => [id, r])))
    if (token !== weatherToken) return
    const notes = {}
    weatherGrids = {}
    for (const [id, r] of results) {
      weatherGrids[id] = weatherGrid(r)
      if (!r.cells.length) notes[id] = r.coverage ? { first: r.coverage.first_date, last: r.coverage.last_date } : {}
    }
    weatherNotes.value = notes
    renderWeather()
    await whenIdle(map)
    prefetchNextDay(on, day)
  } catch (e) {
    // 404: the lake has no weather yet ("not loaded" note); anything else is an error.
    if (token === weatherToken) weatherNotes.value = Object.fromEntries(on.map((id) => [id, e.status === 404 ? {} : { error: e }]))
  }
  if (token === weatherToken) {
    store.setBusy('weather', false)
    weatherLoading.value = false
  }
}

// While the time slider steps through days, have the next one ready.
function prefetchNextDay(on, date) {
  if (!store.day || date >= store.end) return
  const d = new Date(`${date}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + 1)
  const next = d.toISOString().slice(0, 10)
  for (const id of on) loadWeather(id, next).catch(() => {})
}

// Fire hexagons a bit stronger while weather is on, so they stay the protagonists.
// By density (see mapShared.js), lighter once zoomed in to the detections.
// Hexagon borders: a soft neutral gray, so the grid stays in the background and never competes with fire colors.
const HEX_BORDER_COLOR = '#6b7280'
// Anomalies: by how far from usual instead (mapShared.js anomalyOpacity), so usual hexagons stay a faint gray.
const hexFillOpacity = (strong) => {
  const o = store.measure === 'anomaly' ? anomalyOpacity : hexOpacity
  return ['interpolate', ['linear'], ['zoom'], DETAIL_ZOOM - 1, o(strong ? 0.92 : 0.75), DETAIL_ZOOM + 1, o(strong ? 0.55 : 0.35)]
}
// The calendar's color: the fire ramp (hotspots, FRP) or violet / gray / fire (anomalies).
const hexFillColor = () => (store.measure === 'anomaly' ? HEX_ANOMALY_COLOR : HEX_FILL_COLOR)

// Draw the loaded grids in the chosen scope (no request: grids and hexagons are in memory):
// one group of icons per place, thinned to the zoom, fading away from the fires.
function renderWeather() {
  if (!mapReady) return
  const lake = Object.fromEntries(store.weatherLayers.filter((id) => weatherGrids[id]).map((id) => [id, weatherGrids[id]]))
  const selected = Object.fromEntries(Object.entries(selectionGrids(selectionWeather.value, selectionShownDay.value))
    .filter(([id, g]) => g && shownLayers.value.includes(id)))
  const grids = mergedGrids(selected, lake)
  applyContrast() // fire hexagons stronger and grid lighter while weather is on

  const any = Object.keys(grids).length > 0
  const fireCells = [...hexById.values()].filter((f) => f.properties.detections > 0).map((f) => f.properties.h3)
  neighborhood = any && store.weatherScope === 'fires' ? fireNeighborhood(fireCells) : new Map()
  const lakeCandidates = !Object.keys(lake).length ? []
    : store.weatherScope === 'fires' ? fireCandidates(neighborhood) : gridCandidates(lake)
  // Selected hexagons and their neighbors first (selected: ring 1, preferred and fully
  // opaque; neighbors fading with the distance), then the lake's places.
  const rings = store.selectedHexes.length <= SELECTION_RINGS_MAX_HEXAGONS ? SELECTION_RINGS : 0
  selectionArea = Object.keys(selected).length
    ? new Map(store.selectedHexes.flatMap((h) => gridDiskDistances(h, rings).flatMap((ring, k) => ring.map((n) => [n, k])))
      .sort((a, b) => b[1] - a[1]))
    : new Map()
  const selectedCandidates = [...selectionArea].map(([h, k]) => {
    const [lat, lon] = cellToLatLng(h)
    return { lon, lat, ring: k + 1 }
  })
  const candidates = [...selectedCandidates, ...lakeCandidates]
  map.getSource('wx-points').setData(any ? weatherPoints(candidates, grids, map.getZoom()) : EMPTY)
  for (const id of ['temperature', 'precipitation', 'wind']) {
    map.setLayoutProperty(`wx-${id}`, 'visibility', grids[id] ? 'visible' : 'none')
  }
}

// The selected hexagons' weather for the whole range (debounced: Shift+click adds many).
function refreshSelectionWeather() {
  clearTimeout(selectionTimer)
  const token = ++selectionToken
  if (!store.selectedHexes.length || !store.end) {
    selectionWeather.value = null
    selectionNote.value = ''
    renderWeather()
    return
  }
  selectionTimer = setTimeout(async () => {
    const end = store.end
    const earliest = new Date(`${end}T00:00:00Z`)
    earliest.setUTCDate(earliest.getUTCDate() - (SELECTION_MAX_DAYS - 1))
    const start = store.start && store.start > earliest.toISOString().slice(0, 10) ? store.start : earliest.toISOString().slice(0, 10)
    selectionNote.value = t('weather.selectionLoading')
    weatherLoading.value = true
    try {
      const rings = store.selectedHexes.length <= SELECTION_RINGS_MAX_HEXAGONS ? SELECTION_RINGS : 0
      const data = await getSelectionWeather({ h3: store.selectedHexes, start, end, rings })
      if (token !== selectionToken) return
      selectionWeather.value = data
      selectionNote.value = ''
    } catch (e) {
      if (token !== selectionToken) return
      selectionWeather.value = null
      selectionNote.value = t('weather.selectionError', { error: errorMessage(e) })
    }
    weatherLoading.value = false
    renderWeather()
  }, 300)
}

// Jump to the last week with data for a layer (from its "no data" note).
function goToWeather(last) {
  const d = new Date(`${last}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - 6)
  store.setDates(d.toISOString().slice(0, 10), last)
}

// The whole grid of the visible area, with or without detections: just borders,
// not clickable. Built in the browser with a margin, so panning reuses it.
function updateGrid() {
  if (!mapReady) return
  const bounds = map.getBounds()
  const res = hexRes(map.getZoom())
  if (covers(gridShown, bounds, res)) return
  const area = gridArea(bounds, res)
  const fc = gridCells(area)
  map.getSource('grid').setData(fc ?? EMPTY)
  gridShown = fc ? area : null
}

function scheduleRefresh() {
  clearTimeout(debounce)
  scheduled.value = store.view === 'map'
  debounce = setTimeout(() => {
    updateGrid()
    refresh() // sets `loading` right away when it fetches
    refreshWeather()
    scheduled.value = false
  }, 250)
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
    attribution: 'Imagery: <a href="https://www.earthdata.nasa.gov/gibs">EOSDIS GIBS</a>',
  })
  map.addLayer({
    id: 'gibs',
    type: 'raster',
    source: 'gibs',
    layout: { visibility: 'none' },
    // Darkened and desaturated so the fire data stands out.
    paint: { 'raster-brightness-max': 0.65, 'raster-saturation': -0.3 },
  }, labels)

  // Weather icons (hidden until turned on): one point per place with t / rain / wind.
  map.addSource('wx-points', { type: 'geojson', data: EMPTY })
  for (const id of ['temperature', 'precipitation', 'wind']) {
    map.addImage(`wx-${id}`, iconImage(WEATHER_LAYERS[id].icon), { sdf: true })
  }

  map.addSource('grid', { type: 'geojson', data: EMPTY })
  map.addSource('hex', { type: 'geojson', data: EMPTY })
  map.addSource('points', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'hex-fill',
    type: 'fill',
    source: 'hex',
    paint: {
      // Relative scale (mapShared.js): pale to dark over what the query brought.
      'fill-color': hexFillColor(),
      // Lighter when zoomed in, so streets and the detections on top stay readable.
      'fill-opacity': hexFillOpacity(false),
    },
  }, labels)
  // 3D mode: the same hexagons as columns. Heights in meters, per zoom, so a column stays readable from the globe
  // (a res-2 hexagon is ~150 km wide) down to the res-6 grid (~3.7 km).
  map.addLayer({
    id: 'hex-3d', type: 'fill-extrusion', source: 'hex', layout: { visibility: threeD.value ? 'visible' : 'none' },
    filter: ['>', ['get', 'detections'], 0],
    paint: {
      'fill-extrusion-color': hexFillColor(),
      'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'],
        1, ['*', ['^', LEVEL_3D, 1.2], 3_000_000],
        4, ['*', ['^', LEVEL_3D, 1.2], 700_000],
        7, ['*', ['^', LEVEL_3D, 1.2], 80_000],
        10, ['*', ['^', LEVEL_3D, 1.2], 10_000]],
      'fill-extrusion-opacity': 0.9,
      'fill-extrusion-vertical-gradient': true,
    },
  }, labels)
  // While weather is on: a dark outline around the fire hexagons so they stand out.
  map.addLayer({
    id: 'hex-fire-line', type: 'line', source: 'hex', layout: { visibility: 'none' },
    filter: ['>', ['get', 'detections'], 0],
    paint: { 'line-color': '#7f1d1d', 'line-width': 1, 'line-opacity': 0.8 },
  }, labels)
  // Borders come from the full grid, so empty cells show them too.
  map.addLayer({
    id: 'hex-line',
    type: 'line',
    source: 'grid',
    maxzoom: DETAIL_ZOOM,
    paint: { 'line-color': HEX_BORDER_COLOR, 'line-width': HEX_BORDER_WIDTH, 'line-opacity': 0.15 }, // applyContrast() sets them
  }, labels)
  // Zoomed in: the hexagon's limit as a dashed line, so it's never lost.
  map.addLayer({
    id: 'hex-line-dashed',
    type: 'line',
    source: 'grid',
    minzoom: DETAIL_ZOOM,
    paint: { 'line-color': HEX_BORDER_COLOR, 'line-width': 1, 'line-opacity': 0.5, 'line-dasharray': [3, 2] },
  }, labels)
  map.addLayer({
    id: 'points',
    type: 'circle',
    source: 'points',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 12, 7],
      'circle-color': [
        'case', ['==', ['get', 'is_static_source'], true], STATIC_COLOR,
        ['interpolate', ['linear'], ['coalesce', ['get', 'frp'], 0],
          0, '#fde047', 10, '#f97316', 50, '#dc2626', 200, '#7f1d1d'],
      ],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 0.5,
      'circle-opacity': 0.9,
    },
  }, labels)

  // Weather icons above the hexagons (they don't paint the ground): thermometer left,
  // drop right, wind arrow in the middle. Opacity fades with the distance to the fire.
  const icon = (id, extra) => ({
    id: `wx-${id}`, type: 'symbol', source: 'wx-points',
    layout: {
      visibility: 'none', 'icon-image': `wx-${id}`, 'icon-allow-overlap': true, 'icon-ignore-placement': true,
      ...extra.layout,
    },
    paint: { 'icon-opacity': ['get', 'fade'], 'icon-halo-color': '#ffffff', 'icon-halo-width': 1.5, ...extra.paint },
    filter: extra.filter,
  })
  map.addLayer(icon('temperature', {
    filter: ['has', 't'],
    layout: { 'icon-size': TEMPERATURE_SIZE },
    paint: { 'icon-color': colorRamp('temperature', 't'), 'icon-translate': [-13, 0] },
  }), labels)
  map.addLayer(icon('precipitation', {
    filter: ['has', 'rain'],
    layout: { 'icon-size': RAIN_SIZE },
    paint: { 'icon-color': WEATHER_LAYERS.precipitation.color, 'icon-translate': [13, 0] },
  }), labels)
  map.addLayer(icon('wind', {
    filter: ['has', 'wind'],
    layout: { 'icon-size': WIND_SIZE, 'icon-rotate': ['get', 'to'], 'icon-rotation-alignment': 'map' },
    // A wider halo and full opacity near the fire / selection: wind must read at a glance.
    paint: { 'icon-color': colorRamp('wind', 'wind'), 'icon-halo-width': 2.5, 'icon-halo-blur': 0.5,
             'icon-opacity': ['min', 1, ['*', 1.2, ['get', 'fade']]] },
  }), labels)

  // Drawn area: outline of the hexagons it selects (it snaps to the grid).
  map.addSource('zone', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'zone-fill', type: 'fill', source: 'zone',
    paint: { 'fill-color': '#2563eb', 'fill-opacity': 0.06 },
  }, labels)

  // Hexagons open in the panel: outlined on top of everything, at any zoom.
  map.addSource('selected', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'selected-casing', type: 'line', source: 'selected',
    paint: { 'line-color': '#ffffff', 'line-width': 5, 'line-opacity': 0.9 },
  })
  map.addLayer({
    id: 'selected-line', type: 'line', source: 'selected',
    paint: { 'line-color': '#1d4ed8', 'line-width': 2.5 },
  })
  map.addLayer({
    id: 'zone-line', type: 'line', source: 'zone',
    paint: { 'line-color': '#1d4ed8', 'line-width': 2.5 },
  })

  // Area being drawn: clicked points, the shape so far and the hexagons it would take.
  map.addSource('draft', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'draft-cells', type: 'line', source: 'draft', filter: ['==', ['get', 'kind'], 'cells'],
    paint: { 'line-color': '#1d4ed8', 'line-width': 2, 'line-dasharray': [2, 2] },
  })
  map.addLayer({
    id: 'draft-line', type: 'line', source: 'draft', filter: ['==', ['get', 'kind'], 'shape'],
    paint: { 'line-color': '#1d4ed8', 'line-width': 1.5, 'line-opacity': 0.7 },
  })
  map.addLayer({
    id: 'draft-points', type: 'circle', source: 'draft', filter: ['==', ['get', 'kind'], 'point'],
    paint: { 'circle-radius': 5, 'circle-color': '#ffffff', 'circle-stroke-color': '#1d4ed8', 'circle-stroke-width': 2 },
  })
}

// Outline the selected hexagons (polygons from h3-js, no request needed). Many of
// them (an area): thin lines, the area's own outline marks the edge.
function syncSelection() {
  if (!mapReady) return
  const ids = store.selectedHexes
  const features = ids.map((h3) => ({ type: 'Feature', geometry: cellPolygon(h3), properties: { h3 } }))
  map.getSource('selected').setData({ type: 'FeatureCollection', features })
  const many = ids.length > 20
  map.setPaintProperty('selected-line', 'line-width', many ? 0.8 : 2.5)
  map.setPaintProperty('selected-casing', 'line-opacity', many ? 0 : 0.9)
}

function syncZone() {
  if (!mapReady) return
  const geometry = store.zoneCells.length ? cellsOutline(store.zoneCells) : store.zone
  map.getSource('zone').setData(geometry ? { type: 'Feature', geometry, properties: {} } : EMPTY)
}

// ---------------------------------------------------------- drawing an area ---

function startArea() {
  tool.value = 'area'
  draft.value = []
  areaNote.value = null
  map.doubleClickZoom.disable() // quick clicks are points, not zoom
  map.getCanvas().style.cursor = 'crosshair'
  hideTooltip()
}

function stopArea() {
  tool.value = null
  draft.value = []
  drawDraft(null)
  map.doubleClickZoom.enable()
  map.getCanvas().style.cursor = ''
}

// Preview: the points, the shape (with the cursor as the next point) and, from 3 points
// on, the hexagons it would select.
function drawDraft(cursor) {
  if (!mapReady) return
  const pts = cursor ? [...draft.value, cursor] : [...draft.value]
  const features = pts.slice(0, draft.value.length).map((p) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: { kind: 'point' },
  }))
  if (pts.length >= 2) {
    const ring = pts.length >= 3 ? areaPolygon(pts).coordinates[0] : pts
    features.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: ring }, properties: { kind: 'shape' } })
  }
  if (pts.length >= 3) {
    const cells = cellsInArea(areaPolygon(pts))
    if (cells?.length) features.push({ type: 'Feature', geometry: cellsOutline(cells), properties: { kind: 'cells' } })
  }
  map.getSource('draft').setData({ type: 'FeatureCollection', features })
}

function addAreaPoint(lngLat) {
  draft.value = [...draft.value, lngLat.toArray()]
  if (draft.value.length < AREA_POINTS) return drawDraft(null)
  const polygon = areaPolygon(draft.value)
  const cells = cellsInArea(polygon)
  areaNote.value = cells ? { n: cells.length } : { tooBig: true }
  stopArea()
  store.setArea(polygon, cells)
}

function onKey(e) {
  if (e.key !== 'Escape' || !tool.value) return
  e.stopImmediatePropagation() // Esc cancels the drawing, it doesn't also close the panel
  stopArea()
}

function clearZone() {
  areaNote.value = null
  store.clearZone()
}

// "Follow the map": the calendar counts what is on screen. Zoomed out to (almost) the whole world: no zone.
let followTimer = null
function followView() {
  clearTimeout(followTimer)
  followTimer = setTimeout(() => {
    if (!map || store.view !== 'map') return
    const b = map.getBounds()
    const round = (v) => Math.round(v * 10) / 10
    const box = [Math.max(-180, b.getWest()), Math.max(-85, b.getSouth()), Math.min(180, b.getEast()), Math.min(85, b.getNorth())].map(round)
    const world = map.getZoom() < 2 || box[2] - box[0] >= 300
    store.setViewBox(world || box[0] >= box[2] ? null : box)
  }, 400)
}

function polygonBounds(geometry) {
  const bounds = new maplibregl.LngLatBounds()
  for (const [lon, lat] of geometry.coordinates[0]) bounds.extend([lon, lat])
  return bounds
}

function onClick(e) {
  if (tool.value === 'area') return addAreaPoint(e.lngLat)
  const hit = map.queryRenderedFeatures(e.point, { layers: ['hex-fill'] })[0]
  const feature = hit && hexById.get(hit.properties.h3)
  if (!feature) return
  const { h3 } = feature.properties
  if (currentRes < BASE_RES) {
    // Coarse hexagon: zoom into it, at least one level down.
    const camera = map.cameraForBounds(polygonBounds(feature.geometry), { padding: 40 })
    if (!camera) return
    camera.zoom = Math.max(camera.zoom, NEXT_LEVEL_ZOOM[currentRes] ?? camera.zoom)
    // The app's "Reduce motion" option decides alone: `essential` stops MapLibre from also
    // skipping the animation on its own when the OS asks for less motion.
    map.easeTo({ ...camera, duration: prefs.reduceMotion ? 0 : 800, essential: true })
    return
  }
  const additive = e.originalEvent.shiftKey || e.originalEvent.ctrlKey || e.originalEvent.metaKey
  store.selectHex(h3, additive)
}

// Hover: what's under the cursor (a detection first, else its hexagon) and what a click does.
function onHover(e) {
  if (tool.value === 'area') {
    if (draft.value.length && !draftFrame) {
      const cursor = e.lngLat.toArray()
      draftFrame = requestAnimationFrame(() => { draftFrame = null; if (tool.value) drawDraft(cursor) })
    }
    return
  }
  const [hit] = map.queryRenderedFeatures(e.point, { layers: ['points', 'hex-fill'] })
  // Weather under the cursor: anywhere ("Everywhere"), or inside the fires' neighborhood.
  const { lng, lat } = e.lngLat
  const shown = store.weatherScope === 'all' || (currentRes !== null && neighborhood.has(latLngToCell(lat, lng, currentRes)))
  const lakeWeather = store.weatherLayers.length && shown ? weatherTooltip(weatherGrids, lng, lat) : ''
  // Over a selected hexagon: its own (on-demand) weather.
  const overSelected = currentRes !== null && selectionArea.has(latLngToCell(lat, lng, BASE_RES))
  const weather = overSelected && selectionWeather.value
    ? weatherTooltip(selectionGrids(selectionWeather.value, selectionShownDay.value), lng, lat) || lakeWeather
    : lakeWeather
  if (!hit && !weather) return hideTooltip()
  map.getCanvas().style.cursor = hit ? 'pointer' : ''
  const html = !hit ? '' : hit.layer.id === 'points'
    ? pointPopup(hit.properties)
    : hexPopup(hit.properties, (currentRes < BASE_RES ? t('map.popup.zoomHint') : t('map.popup.openHint')))
  const day = `<span class="popup-muted">${t('weather.tooltipDay', { date: date(store.imageryDate) })}</span>`
  tooltip.setLngLat(e.lngLat)
    .setHTML([html, weather && `${weather}<br>${day}`].filter(Boolean).join('<hr class="popup-rule">'))
    .addTo(map)
}

function hideTooltip() {
  tooltip.remove()
  if (!tool.value) map.getCanvas().style.cursor = ''
}

function registerEvents() {
  tooltip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: '280px' })
  map.on('click', onClick)
  map.on('mousemove', onHover)
  map.on('mouseout', hideTooltip)
  map.on('movestart', hideTooltip)
  // Only while shown: hidden behind the curtain view, the curtain owns the camera.
  map.on('moveend', () => { if (store.view === 'map') store.setCamera(map.getCenter().toArray(), map.getZoom()) })
  map.on('moveend', scheduleRefresh)
  map.on('moveend', followView)
}

// Keep the GIBS layer in line with the chosen image type and the end of the date range.
function syncImagery() {
  if (!mapReady) return
  const img = store.imagery // { layer, date } or null
  map.setLayoutProperty('gibs', 'visibility', img ? 'visible' : 'none')
  if (img) map.getSource('gibs').setTiles([gibsUrl(img.layer.id, img.date)])
  noImagery.value = store.basemap !== 'vector' && !img
}

// Hexagon borders and fill. High contrast (accessibility menu) makes them stronger. The borders
// stay the same soft line whether the contour lines are on or not. With weather on, fire hexagons get stronger and an outline, and the grid fades, so fire
// stays the protagonist. The fill opacity always follows the fire density (mapShared.js).
function applyContrast() {
  if (!mapReady) return
  const on = prefs.contrast
  const weatherOn = store.weatherLayers.length > 0
  // A whole pixel, light (mapShared.js HEX_BORDER_WIDTH), so they look alike on every screen.
  // Soft neutral gray always (contour lines on or off); high contrast makes them dark and stronger.
  map.setPaintProperty('hex-line', 'line-color', on ? '#7c2d12' : HEX_BORDER_COLOR)
  map.setPaintProperty('hex-line-dashed', 'line-color', on ? '#7c2d12' : HEX_BORDER_COLOR)
  map.setPaintProperty('hex-line', 'line-opacity', on ? 0.9 : weatherOn ? 0.08 : 0.15)
  map.setPaintProperty('hex-line', 'line-width', on ? 1.2 : HEX_BORDER_WIDTH)
  map.setPaintProperty('hex-line-dashed', 'line-opacity', on ? 0.9 : 0.5)
  map.setPaintProperty('hex-line-dashed', 'line-width', on ? 2.5 : 1)
  map.setPaintProperty('hex-fill', 'fill-opacity', hexFillOpacity(weatherOn || on))
  map.setPaintProperty('hex-fill', 'fill-color', hexFillColor())
  map.setPaintProperty('hex-3d', 'fill-extrusion-color', hexFillColor())
  map.setLayoutProperty('hex-fire-line', 'visibility', weatherOn ? 'visible' : 'none')
  contourContrast(map, on)
}

// Contour lines toggle (relief.js): under the hexagons, labels under the detection points.
function syncContours() {
  if (!mapReady) return
  setContours(map, prefs.contours, { linesBelow: 'hex-fill', labelsBelow: 'points' })
  applyContrast()
}

// Enter on the focused map = a click at its center (the crosshair).
function onMapKey(e) {
  if (e.key !== 'Enter') return
  e.preventDefault()
  const canvas = map.getCanvas()
  const point = new maplibregl.Point(canvas.clientWidth / 2, canvas.clientHeight / 2)
  onClick({ point, lngLat: map.unproject(point), originalEvent: e })
}

onMounted(() => {
  map = new maplibregl.Map({
    container: container.value,
    style: STYLE,
    center: store.camera.center, // kept in the store so the curtain view opens at the same place
    zoom: store.camera.zoom,
    renderWorldCopies: false,
    boxZoom: false, // Shift + click selects hexagons; box zoom would swallow it
    locale: { ...mapLocale(), 'Map.Title': t('map.label') }, // the canvas' label for screen readers
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => {
    addLayers()
    registerEvents()
    mapReady = true
    mapRef.value = map
    localizeBasemap(map, prefs.locale)
    applyContrast()
    syncContours()
    untrackSources = trackMapSources(map, { contours: 'contours', gibs: 'imagery', openmaptiles: 'basemap' })
    map.getCanvas().addEventListener('keydown', onMapKey)
    syncImagery()
    syncSelection()
    syncZone()
    updateGrid()
    refresh()
    refreshWeather()
  })
  // The panel opening/closing changes the map's width.
  resizer = new ResizeObserver(() => { if (container.value.clientWidth) map.resize() })
  resizer.observe(container.value)
  window.addEventListener('keydown', onKey, { capture: true })
})

onBeforeUnmount(() => {
  controller?.abort()
  weatherToken++
  store.setBusy('map', false)
  store.setBusy('weather', false)
  clearTimeout(debounce)
  clearTimeout(followTimer)
  resizer?.disconnect()
  window.removeEventListener('keydown', onKey, { capture: true })
  cancelAnimationFrame(draftFrame)
  untrackSources?.()
  map?.remove()
})

watch(() => [store.values, store.ready, store.dataset, store.queryParams], refresh)
// The calendar's color changed: the map colors the same way (the paint switches with the new values, in refresh).
watch(() => store.measure, refresh)
// Back from the curtain view: the container was hidden, so resize, catch up with
// where the curtain was left, and load what changed meanwhile.
watch(() => store.view, (view) => {
  if (view !== 'map' || !map) return
  const camera = store.camera // read first: resize() fires moveend, which saves this map's old camera
  requestAnimationFrame(() => {
    map.resize()
    map.jumpTo(camera)
    refresh()
    refreshWeather()
  })
})
// Layers turned on/off, or another day: reload the weather.
watch(() => [store.weatherLayers, store.imageryDate], refreshWeather)
watch(() => [store.selectedHexes, store.start, store.end], refreshSelectionWeather)
watch(() => store.weatherScope, renderWeather)
// Image type, satellite or day changed: update the imagery.
// Watch id and date separately: pickLayer() returns a new object every time.
watch(() => [store.basemap, store.imagery?.layer.id, store.imagery?.date], syncImagery)
watch(() => store.selectedHexes, syncSelection)
watch(() => [store.zone, store.zoneCells], syncZone)
// A preset region was picked: zoom to it (only while shown; the curtain view fits itself).
watch(() => store.fitTo, (f) => { if (map && f && store.view === 'map') fitRegion(map, f.bbox, prefs.reduceMotion) })
watch(() => store.zone, (zone) => { if (!zone) areaNote.value = null })
watch(() => prefs.contrast, applyContrast)
watch(() => prefs.contours, syncContours)
watch(() => prefs.locale, (locale) => {
  if (!mapReady) return
  localizeBasemap(map, locale)
  map.getCanvas().setAttribute('aria-label', t('map.label'))
})
</script>

<template>
  <div class="map-wrap">
    <div ref="container" class="map"></div>
    <!-- Where Enter acts when the map has the keyboard focus. -->
    <div class="crosshair" aria-hidden="true"></div>

    <div class="toolbar">
      <button :aria-pressed="tool === 'area'" :class="{ active: tool === 'area' }" @click="tool === 'area' ? stopArea() : startArea()">
        <span aria-hidden="true">▱</span> {{ $t('map.area') }}
      </button>
      <button v-if="store.zone" @click="clearZone"><span aria-hidden="true">✕</span> {{ $t('map.clearArea') }}</button>
      <button :aria-pressed="threeD" :class="{ active: threeD }" :title="$t('map.view3dHint')" @click="toggle3d">
        {{ $t('map.view3d') }}
      </button>
      <button :aria-pressed="prefs.contours" :class="{ active: prefs.contours }" @click="prefs.contours = !prefs.contours">
        <span aria-hidden="true">⛰</span> {{ $t('map.contours') }}
      </button>
      <span v-if="tool" class="hint" role="status">{{ $t('map.areaHint', AREA_POINTS - draft.length) }}</span>
      <span v-else-if="areaNote" class="hint" role="status">
        {{ areaNote.tooBig ? $t('map.areaTooBig', { max: fmt(MAX_SELECTION) }) : $t('map.areaSelected', { n: fmt(areaNote.n) }) }}
      </span>
    </div>

    <section v-if="shownLayers.length || selectionNote" class="legend" :aria-label="$t('weather.legend')">
      <span v-if="selectionNote" class="legend-note">{{ selectionNote }}</span>
      <span v-else-if="selectionWeather && selectionShownDay && selectionShownDay !== store.imageryDate" class="legend-note">
        {{ $t('weather.selectionLatest', { date: date(selectionShownDay) }) }}
      </span>
      <div v-for="id in shownLayers" :key="id" class="legend-item">
        <span class="legend-icons">
          <span class="legend-title">{{ WEATHER_LAYERS[id].title }}</span>
          <span v-for="v in WEATHER_LAYERS[id].legend" :key="v">
            <svg
              viewBox="0 0 24 24" aria-hidden="true"
              :width="id === 'temperature' ? 16 : 26 * (id === 'precipitation' ? rainSize(v) : windSize(v))"
              :height="id === 'temperature' ? 16 : 26 * (id === 'precipitation' ? rainSize(v) : windSize(v))"
              :style="id === 'wind' ? { transform: 'rotate(45deg)' } : null"
            >
              <path :d="WEATHER_LAYERS[id].icon" :fill="id === 'precipitation' ? WEATHER_LAYERS[id].color : colorAt(id, v)" />
            </svg>
            {{ fmt(v) }}{{ WEATHER_LAYERS[id].unit }}
          </span>
        </span>
        <span v-if="weatherNotes[id]" class="legend-note">
          <template v-if="weatherNotes[id].error">{{ errorMessage(weatherNotes[id].error) }}</template>
          <template v-else-if="weatherNotes[id].last">
            {{ $t('weather.noDataOn', { date: date(store.imageryDate), first: date(weatherNotes[id].first), last: date(weatherNotes[id].last) }) }}
            <button @click="goToWeather(weatherNotes[id].last)">{{ $t('weather.goTo', { date: date(weatherNotes[id].last) }) }}</button>
          </template>
          <template v-else>{{ $t('weather.notLoaded') }}</template>
        </span>
      </div>
      <span class="legend-fade">
        {{ store.day ? $t('weather.daySlider', { date: date(store.imageryDate) }) : $t('weather.dayEnd', { date: date(store.imageryDate) }) }}
        <template v-if="store.weatherScope === 'fires'">{{ $t('weather.fading') }}</template>
      </span>
    </section>

    <div v-if="loading || error || info || noImagery" class="status" role="status">
      <span v-if="loading" class="spinner" role="img" :aria-label="$t('common.loading')"></span>
      <span v-if="error" class="error">{{ errorMessage(error) }}</span>
      <span v-else-if="info">
        {{ $t('map.summary', {
          detections: fmt(info.total), hexagons: fmt(info.hexagons),
          grid: info.res === BASE_RES ? $t('map.gridBase') : $t('map.gridRes', { res: info.res }),
        }) }}{{ info.truncated ? $t('map.pointsTruncated', { n: fmt(POINTS_LIMIT) }) : '' }}
      </span>
      <span v-if="noImagery" class="warn">{{ $t('controls.noImagery') }}</span>
      <FireScale v-if="info && fireTicks && !error" :ticks="fireTicks" :mode="store.measure" :no-years="noYears" />
    </div>
    <MouseCoords :maps="mapRef ? [mapRef] : []" />
  </div>
</template>

<style scoped>
.map-wrap { position: relative; height: 100%; }
.map { position: absolute; inset: 0; }
.crosshair {
  display: none; position: absolute; left: 50%; top: 50%; width: 22px; height: 22px; margin: -11px 0 0 -11px;
  pointer-events: none; border: 2px solid var(--focus); border-radius: 50%; box-shadow: 0 0 0 2px #ffffff;
}
.map-wrap:has(.maplibregl-canvas:focus-visible) .crosshair { display: block; }
.toolbar {
  position: absolute; top: 12px; left: 12px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
}
.toolbar button {
  background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 10px; font: inherit; font-size: 0.8125rem; cursor: pointer; box-shadow: var(--shadow);
}
.toolbar button.active { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.hint {
  background: var(--panel); padding: 5px 8px; border-radius: 6px; font-size: 0.75rem; color: var(--muted);
  box-shadow: var(--shadow);
}
.status {
  position: absolute; bottom: 28px; left: 12px; right: 12px; max-width: fit-content; display: flex; gap: 8px;
  align-items: center; flex-wrap: wrap; background: var(--panel); padding: 6px 10px; border-radius: 6px;
  font-size: 0.8125rem; box-shadow: var(--shadow);
}
/* Top right, under the zoom buttons: the bottom is taken by the status line and attribution. */
.legend {
  position: absolute; top: 84px; right: 10px; max-width: min(340px, calc(100% - 24px)); display: flex;
  flex-direction: column; gap: 4px; background: var(--panel); padding: 6px 10px; border-radius: 6px;
  box-shadow: var(--shadow); font-size: 0.6875rem;
}
.legend-item { display: flex; flex-direction: column; gap: 2px; }
.legend-title { color: var(--text); font-weight: 600; min-width: 58px; }
.legend-bar { height: 8px; border-radius: 4px; }
.legend-ticks { display: flex; justify-content: space-between; color: var(--muted); font-variant-numeric: tabular-nums; }
.legend-note { color: var(--muted); }
.legend-icons { display: flex; gap: 8px; align-items: center; color: var(--muted); flex-wrap: wrap; }
.legend-icons span { display: inline-flex; align-items: center; gap: 2px; }
.legend-fade { color: var(--muted); font-size: 0.625rem; }
.legend-note button {
  margin-top: 3px; display: block; font: inherit; font-size: 0.6875rem; background: none; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 5px; padding: 2px 6px; cursor: pointer;
}
@media (max-width: 760px) { .legend { left: 12px; top: 56px; } }
.error { color: var(--danger); }
.warn { color: var(--muted); }
.spinner {
  width: 12px; height: 12px; border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 2.4s; } }
</style>