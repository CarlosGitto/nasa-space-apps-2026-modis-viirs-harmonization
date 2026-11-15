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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getDetections, getHexagons, getSelectionWeather, getWeatherMap } from '../api'
import { gibsUrl } from '../gibs'
import {
  MAX_SELECTION, areaPolygon, cellPolygon, cellsInArea, cellsOutline, covers, gridArea, gridCells,
} from '../hexGrid'
import { getHexagonEdgeLengthAvg } from 'h3-js'
import {
  BASE_RES, DETAIL_ZOOM, EMPTY, HEX_FILL_COLOR, POINTS_LIMIT, STATIC_COLOR, STYLE,
  fmt, hexOpacity, hexPopup, hexRes, labelLayerId, maplibregl, pointPopup, viewportBbox, whenIdle, withDensity,
} from '../mapShared'
import { cellToLatLng, latLngToCell } from 'h3-js'
import {
  RAIN_SIZE, TEMPERATURE_SIZE, WEATHER_IDS, WEATHER_LAYERS, WIND_SIZE, colorAt, mergedGrids, selectionDay, selectionGrids, colorRamp, fireCandidates, fireNeighborhood, gridCandidates,
  iconImage, rainSize, responseCache, weatherGrid, weatherPoints, weatherRequest, weatherTooltip, windSize,
} from '../mapWeather'
import { useFiltersStore } from '../stores/filters'

const store = useFiltersStore()
const container = ref(null)
const AREA_POINTS = 4
const tool = ref(null) // 'area' while drawing an area, else null
const draft = ref([]) // [lng, lat] of the points clicked so far
const areaNote = ref('') // e.g. area too big to select its hexagons
const loading = ref(false)
const error = ref(null)
const info = ref('')
const imageryNote = ref('') // warning when the chosen imagery has no picture for that date
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
// The layers drawn: the ones turned on, or all three when only a selection has weather.
const shownLayers = computed(() =>
  store.weatherLayers.length ? store.weatherLayers : selectionWeather.value ? WEATHER_IDS : [])
// The selection's day drawn: the map's day, or the latest before it POWER has published.
const selectionShownDay = computed(() => selectionDay(selectionWeather.value, store.imageryDate))

// Smallest zoom showing a finer grid than `res`: clicking a coarse hexagon always goes down a level.
const NEXT_LEVEL_ZOOM = { 0: 3, 1: 3, 2: 3, 3: 4.5, 4: 6, 5: 7.5 }

async function refresh() {
  // Hidden while the MODIS | VIIRS curtain is shown: don't fetch for nothing.
  if (!mapReady || store.view !== 'map' || !store.dataset || !store.currentDataset?.available) return
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
    const [fc, points] = await Promise.all([
      getHexagons(store.dataset, { ...params, bbox: hexBbox, res }, signal),
      zoom >= DETAIL_ZOOM ? getDetections(store.dataset, { ...params, limit: POINTS_LIMIT }, signal) : null,
    ])
    currentRes = res
    withDensity(fc, store.queryParams.start, store.queryParams.end)
    hexById = new Map(fc.features.map((f) => [f.properties.h3, f]))
    map.getSource('hex').setData(fc)
    renderWeather() // the halo follows the fire hexagons on screen
    map.getSource('points').setData(points ?? EMPTY)
    const total = fc.features.reduce((s, f) => s + f.properties.detections, 0)
    const grid = res === BASE_RES ? 'base grid, H3 res 6' : `H3 res ${res}`
    info.value = `${fmt(total)} detections in ${fmt(fc.features.length)} hexagons (${grid})`
    if (points?.truncated) info.value += ` · showing the ${fmt(POINTS_LIMIT)} most recent points`
    error.value = null
    await whenIdle(map) // drawn (data and imagery) before the time slider moves on
    if (!signal.aborted) store.setBusy('map', false)
  } catch (e) {
    if (e.name === 'AbortError') return // a newer refresh owns the busy flag
    error.value = e.message
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
    renderWeather()
    return
  }
  store.setBusy('weather', true)
  try {
    const date = store.imageryDate
    const results = await Promise.all(on.map((id) => loadWeather(id, date).then((r) => [id, r])))
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
    prefetchNextDay(on, date)
  } catch (e) {
    if (token === weatherToken) weatherNotes.value = Object.fromEntries(on.map((id) => [id, { error: e.message }]))
  }
  if (token === weatherToken) store.setBusy('weather', false)
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
const HEX_OPACITY = ['interpolate', ['linear'], ['zoom'], DETAIL_ZOOM - 1, hexOpacity(0.75), DETAIL_ZOOM + 1, hexOpacity(0.35)]
const HEX_OPACITY_OVER_WEATHER = ['interpolate', ['linear'], ['zoom'], DETAIL_ZOOM - 1, hexOpacity(0.92), DETAIL_ZOOM + 1, hexOpacity(0.55)]

// Draw the loaded grids in the chosen scope (no request: grids and hexagons are in memory):
// one group of icons per place, thinned to the zoom, fading away from the fires.
function renderWeather() {
  if (!mapReady) return
  const lake = Object.fromEntries(store.weatherLayers.filter((id) => weatherGrids[id]).map((id) => [id, weatherGrids[id]]))
  const selected = Object.fromEntries(Object.entries(selectionGrids(selectionWeather.value, selectionShownDay.value))
    .filter(([id, g]) => g && shownLayers.value.includes(id)))
  const grids = mergedGrids(selected, lake)
  const active = store.weatherLayers.length > 0
  map.setPaintProperty('hex-fill', 'fill-opacity', active ? HEX_OPACITY_OVER_WEATHER : HEX_OPACITY)
  map.setLayoutProperty('hex-fire-line', 'visibility', active ? 'visible' : 'none')
  map.setPaintProperty('hex-line', 'line-opacity', active ? 0.15 : 0.4)

  const any = Object.keys(grids).length > 0
  const fireCells = [...hexById.values()].filter((f) => f.properties.detections > 0).map((f) => f.properties.h3)
  neighborhood = any && store.weatherScope === 'fires' ? fireNeighborhood(fireCells) : new Map()
  const lakeCandidates = !Object.keys(lake).length ? []
    : store.weatherScope === 'fires' ? fireCandidates(neighborhood) : gridCandidates(lake)
  // Selected hexagons first (ring 1: preferred and fully opaque), then the lake's places.
  const selectedCandidates = Object.keys(selected).length
    ? store.selectedHexes.map((h) => { const [lat, lon] = cellToLatLng(h); return { lon, lat, ring: 1 } })
    : []
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
    selectionNote.value = 'Loading the weather of the selection (NASA POWER)…'
    try {
      const data = await getSelectionWeather({ h3: store.selectedHexes, start, end })
      if (token !== selectionToken) return
      selectionWeather.value = data
      selectionNote.value = ''
    } catch (e) {
      if (token !== selectionToken) return
      selectionWeather.value = null
      selectionNote.value = `Selection weather: ${e.message}`
    }
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
  debounce = setTimeout(() => {
    updateGrid()
    refresh()
    refreshWeather()
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
      // Log scale: a few cells have thousands of detections, most have a handful.
      'fill-color': HEX_FILL_COLOR,
      // Lighter when zoomed in, so streets and the detections on top stay readable.
      'fill-opacity': HEX_OPACITY,
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
    paint: { 'line-color': '#7c2d12', 'line-width': 0.3, 'line-opacity': 0.4 },
  }, labels)
  // Zoomed in: the hexagon's limit as a dashed line, so it's never lost.
  map.addLayer({
    id: 'hex-line-dashed',
    type: 'line',
    source: 'grid',
    minzoom: DETAIL_ZOOM,
    paint: { 'line-color': '#7c2d12', 'line-width': 1.5, 'line-opacity': 0.8, 'line-dasharray': [3, 2] },
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
    paint: { 'icon-color': colorRamp('wind', 'wind') },
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
  areaNote.value = ''
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
  areaNote.value = cells
    ? `${fmt(cells.length)} hexagons selected`
    : `Area too big to select its hexagons (max ${fmt(MAX_SELECTION)}): used as a filter only`
  stopArea()
  store.setArea(polygon, cells)
}

function onKey(e) {
  if (e.key !== 'Escape' || !tool.value) return
  e.stopImmediatePropagation() // Esc cancels the drawing, it doesn't also close the panel
  stopArea()
}

function clearZone() {
  areaNote.value = ''
  store.clearZone()
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
    map.easeTo({ ...camera, duration: 800 })
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
  const overSelected = currentRes !== null && store.selectedHexes.includes(latLngToCell(lat, lng, BASE_RES))
  const weather = overSelected && selectionWeather.value
    ? weatherTooltip(selectionGrids(selectionWeather.value, selectionShownDay.value), lng, lat) || lakeWeather
    : lakeWeather
  if (!hit && !weather) return hideTooltip()
  map.getCanvas().style.cursor = hit ? 'pointer' : ''
  const html = !hit ? '' : hit.layer.id === 'points'
    ? pointPopup(hit.properties)
    : hexPopup(hit.properties, currentRes < BASE_RES ? 'Click to zoom in' : 'Click to open · Shift+click to add')
  const day = `<span style="color:#6b7280">Weather ${store.imageryDate} (UTC)</span>`
  tooltip.setLngLat(e.lngLat).setHTML([html, weather && `${weather}<br>${day}`].filter(Boolean).join('<hr style="margin:4px 0;border:none;border-top:1px solid #e5e7eb">')).addTo(map)
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
}

// Keep the GIBS layer in line with the chosen image type and the end of the date range.
function syncImagery() {
  if (!mapReady) return
  const img = store.imagery // { layer, date } or null
  map.setLayoutProperty('gibs', 'visibility', img ? 'visible' : 'none')
  if (img) map.getSource('gibs').setTiles([gibsUrl(img.layer.id, img.date)])
  imageryNote.value = store.basemap !== 'vector' && !img ? 'No satellite imagery for that date' : ''
}

onMounted(() => {
  map = new maplibregl.Map({
    container: container.value,
    style: STYLE,
    center: store.camera.center, // kept in the store so the curtain view opens at the same place
    zoom: store.camera.zoom,
    renderWorldCopies: false,
    boxZoom: false, // Shift + click selects hexagons; box zoom would swallow it
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => {
    addLayers()
    registerEvents()
    mapReady = true
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
  resizer?.disconnect()
  window.removeEventListener('keydown', onKey, { capture: true })
  cancelAnimationFrame(draftFrame)
  map?.remove()
})

watch(() => [store.dataset, store.queryParams], refresh)
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
watch(() => store.zone, (zone) => { if (!zone) areaNote.value = '' })
</script>

<template>
  <div class="map-wrap">
    <div ref="container" class="map"></div>

    <div class="toolbar">
      <button :class="{ active: tool === 'area' }" @click="tool === 'area' ? stopArea() : startArea()">
        ▱ Area (4 points)
      </button>
      <button v-if="store.zone" @click="clearZone">✕ Clear area</button>
      <span v-if="tool" class="hint">
        Click {{ AREA_POINTS - draft.length }} more point{{ AREA_POINTS - draft.length === 1 ? '' : 's' }} · Esc cancels
      </span>
      <span v-else-if="areaNote" class="hint">{{ areaNote }}</span>
    </div>

    <div v-if="shownLayers.length || selectionNote" class="legend" aria-label="Weather legend">
      <span v-if="selectionNote" class="legend-note">{{ selectionNote }}</span>
      <span v-else-if="selectionWeather && selectionShownDay && selectionShownDay !== store.imageryDate" class="legend-note">
        Selected hexagons: {{ selectionShownDay }}, the latest NASA POWER has (~2 days behind)
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
            {{ v }}{{ WEATHER_LAYERS[id].unit }}
          </span>
        </span>
        <span v-if="weatherNotes[id]" class="legend-note">
          <template v-if="weatherNotes[id].error">{{ weatherNotes[id].error }}</template>
          <template v-else-if="weatherNotes[id].last">
            No data on {{ store.imageryDate }} · loaded {{ weatherNotes[id].first }} → {{ weatherNotes[id].last }}
            <button @click="goToWeather(weatherNotes[id].last)">Go to {{ weatherNotes[id].last }}</button>
          </template>
          <template v-else>No data loaded yet (weather_ingest DAG)</template>
        </span>
      </div>
      <span class="legend-fade">
        {{ store.imageryDate }} UTC ({{ store.day ? 'slider day' : 'end of range' }}) · NASA POWER
        <template v-if="store.weatherScope === 'fires'"> · fading away from fires</template>
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
/* Top right, under the zoom buttons: the bottom is taken by the status line and attribution. */
.legend {
  position: absolute; top: 84px; right: 10px; max-width: min(340px, calc(100% - 24px)); display: flex;
  flex-direction: column; gap: 4px; background: var(--panel); padding: 6px 10px; border-radius: 6px;
  box-shadow: var(--shadow); font-size: 11px;
}
.legend-item { display: flex; flex-direction: column; gap: 2px; }
.legend-title { color: var(--text); font-weight: 600; min-width: 58px; }
.legend-bar { height: 8px; border-radius: 4px; }
.legend-ticks { display: flex; justify-content: space-between; color: var(--muted); font-variant-numeric: tabular-nums; }
.legend-note { color: var(--muted); }
.legend-icons { display: flex; gap: 8px; align-items: center; color: var(--muted); flex-wrap: wrap; }
.legend-icons span { display: inline-flex; align-items: center; gap: 2px; }
.legend-fade { color: var(--muted); font-size: 10px; }
.legend-note button {
  margin-top: 3px; display: block; font: inherit; font-size: 11px; background: none; color: var(--accent);
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