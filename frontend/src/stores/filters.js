// The shared "coordinator": which dataset, which dates, which zone and which basemap.
// Components watch `dataset` / `queryParams` and refetch when they change.
// zone === null means the whole world; day === null means every day of start..end.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getHarmonizedDaily, getHarmonizedMonthly, listDatasets } from '../api'
import { errorMessage } from '../format'
import { pickLayer } from '../gibs'
import { REGIONS, bbox } from '../regions'

const DEFAULT_DAYS = 7
const PREFERRED_DATASET = 'viirs_best' // continuous: SP where published, NRT after it

const isoDate = (d) => d.toISOString().slice(0, 10)
const addDays = (iso, days) => {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return isoDate(d)
}

export const useFiltersStore = defineStore('filters', () => {
  const datasets = ref([])
  const dataset = ref(null)
  const start = ref(null)
  const end = ref(null)
  const zone = ref(null)
  const zoneCells = ref([]) // res-6 cells of a drawn area ([] when none, or too many to list)
  // 'view' = follow the map: the calendar counts what is on screen (zone stays null, the map isn't filtered);
  // a preset region id (regions.js); 'custom' for a drawn area; null = the whole world.
  const region = ref('view')
  const viewZone = ref(null) // the main map's viewport as a Polygon, null when zoomed out to the world
  // The zone the calendar and the time slider count: the viewport while following the map, else the region.
  const statsZone = computed(() => (region.value === 'view' ? viewZone.value : zone.value))
  // The maps fit their camera to this when it changes: { bbox: [w, s, e, n] | null (world), n }.
  const fitTo = ref(null)
  const error = ref(null)
  const basemap = ref('vector') // 'vector' (OpenFreeMap) or an image type: 'truecolor' | 'fire'
  const day = ref(null) // one day within start..end picked on the time slider, or null = the whole range
  const view = ref('map') // 'map' | 'compare' (MODIS | VIIRS curtain)
  // What the fire calendar and the main map show: 'harmonized' (one series 2000 -> today in MODIS units, MODIS
  // before 2012-02, VIIRS S-NPP / k after) or 'raw' (the dataset chosen in the controls, as published).
  const values = ref('raw')
  // How the calendar and the main map color: 'detections' (hotspots), 'frp' (fire power) or 'anomaly' (against the
  // same dates of the other years). Picked in the calendar; the map follows.
  const measure = ref('detections')
  const setMeasure = (m) => { measure.value = m }
  const harmonizedReady = ref(null) // null until known; false when the ETL hasn't built the harmonized series
  // Days the harmonized series covers in the lake: from its first month (2000 with the whole MODIS archive) to its
  // last day (it follows the science-quality data, so it can end some days before the near-real-time datasets).
  const harmonizedFirst = ref(null)
  const harmonizedLast = ref(null)
  const camera = ref({ center: [0, 20], zoom: 1.5 }) // shared by both views
  const selectedHexes = ref([]) // H3 res-6 ids open in the hexagon panel, in click order
  // Weather layers drawn on the map ('temperature' | 'precipitation' | 'wind'), for imageryDate.
  const weatherLayers = ref([])
  // Where they're drawn: 'fires' (fire hexagons and their neighbors) or 'all' (the whole field).
  const weatherScope = ref('fires')
  // Maps still loading a change (data and imagery): the time slider waits for them before
  // showing the next day, so the picture always matches the day on the slider.
  const busy = ref({}) // view -> true while loading

  const isBusy = computed(() => Object.values(busy.value).some(Boolean))
  const setBusy = (key, value) => { busy.value = { ...busy.value, [key]: value } }

  const currentDataset = computed(() => datasets.value.find((d) => d.id === dataset.value) ?? null)
  const harmonized = computed(() => values.value === 'harmonized')

  // Days that can be shown: the chosen dataset's, or the harmonized series' (2000 to the latest data of any sensor).
  const span = computed(() => {
    if (!harmonized.value) {
      const d = currentDataset.value
      return d?.available ? { start: d.start_date, end: d.end_date } : null
    }
    return harmonizedFirst.value && harmonizedLast.value ? { start: harmonizedFirst.value, end: harmonizedLast.value } : null
  })
  // Whether the map and the calendar have something to ask for.
  const ready = computed(() => (harmonized.value ? harmonizedReady.value === true : !!currentDataset.value?.available))

  // Curtain: MODIS on the left, VIIRS on the right, same product (NRT or SP) as the chosen dataset.
  // Harmonized: both sides in MODIS units (MODIS bow-tie merged and size-corrected; VIIRS S-NPP / k of each hexagon).
  const compareHarmonized = ref(false)
  // Hexagon report (fichas): raw values, or harmonized (MODIS units). Separate from the Compare switch on purpose.
  const hexHarmonized = ref(false)
  const compareSides = computed(() => {
    if (compareHarmonized.value) {
      const side = (sensor) => datasets.value.find((d) => d.id === `${sensor}_harmonized`) ?? { id: `${sensor}_harmonized`, available: false }
      return { product: 'harmonized', left: side('modis'), right: side('viirs') }
    }
    const product = currentDataset.value?.product ?? 'nrt'
    const side = (sensor) => datasets.value.find((d) => d.id === `${sensor}_${product}`) ?? { id: `${sensor}_${product}`, available: false }
    return { product, left: side('modis'), right: side('viirs') }
  })

  // The single day both curtain sides show imagery for: the slider's day, else the end of the range.
  const imageryDate = computed(() => day.value ?? end.value)

  // Filters sent to the backend for the map. No zone -> no `zone` param -> whole world.
  // A day picked on the time slider narrows the range to that day.
  const queryParams = computed(() => ({
    start: day.value ?? start.value,
    end: day.value ?? end.value,
    zone: zone.value,
  }))

  // GIBS imagery to show: { layer, date }, or null (vector basemap, or no imagery for that date).
  // One day only: the slider's day, else the end of the selected range. The satellite is picked by date.
  const imagery = computed(() =>
    basemap.value === 'vector' ? null : pickLayer(basemap.value, day.value ?? end.value),
  )

  function lastDays(info) {
    day.value = null
    end.value = info.end_date
    start.value = addDays(info.end_date, -(DEFAULT_DAYS - 1))
    if (start.value < info.start_date) start.value = info.start_date
  }

  async function loadDatasets() {
    try {
      datasets.value = await listDatasets()
      error.value = null
    } catch (e) {
      error.value = errorMessage(e) // shown in the user's language (App.vue)
      return
    }
    const available = datasets.value.filter((d) => d.available)
    const pick = available.find((d) => d.id === PREFERRED_DATASET) ?? available[0]
    if (pick) {
      dataset.value = pick.id
      lastDays(pick)
    }
    // The harmonized series is the calendar's default once the ETL has built it. Where it starts depends on what
    // was loaded into the lake (2000 with the whole MODIS archive): its first month.
    try {
      const months = await getHarmonizedMonthly({})
      if (!months.length) throw new Error('empty harmonized series')
      const month = (r) => `${r.year}-${String(r.month).padStart(2, '0')}`
      const lastMonth = months[months.length - 1]
      const days = await getHarmonizedDaily({ start: `${month(lastMonth)}-01`, end: isoDate(new Date(Date.UTC(lastMonth.year, lastMonth.month, 0))) })
      harmonizedFirst.value = `${month(months[0])}-01`
      harmonizedLast.value = days.length ? days[days.length - 1].date : isoDate(new Date(Date.UTC(lastMonth.year, lastMonth.month, 0)))
      harmonizedReady.value = true
      values.value = 'harmonized'
      hexHarmonized.value = true
      // Open on the latest complete month: a calendar starts on a month, not on a few days.
      const last = harmonizedLast.value
      const lastOfIt = isoDate(new Date(Date.UTC(+last.slice(0, 4), +last.slice(5, 7), 0)))
      const endMonth = lastOfIt === last ? last : addDays(`${last.slice(0, 7)}-01`, -1)
      setRange(`${endMonth.slice(0, 7)}-01`, endMonth)
    } catch {
      harmonizedReady.value = false
    }
  }

  function setValues(v) {
    if (v === 'harmonized' && harmonizedReady.value === false) return
    values.value = v
    hexHarmonized.value = v === 'harmonized' // the hexagon report follows (it keeps its own switch)
    // Back to a dataset that doesn't cover the period (say, 2005 harmonized -> VIIRS raw): its last days.
    const s = span.value
    if (s && (!start.value || !end.value || end.value < s.start || start.value > s.end)) lastDays(currentDataset.value)
  }

  // The fire calendar: a period (and optionally one day in it, for the time slider), clamped to what can be shown.
  // Returns false when nothing of it can be shown.
  function setRange(newStart, newEnd, newDay = null) {
    const s = span.value
    if (!s) return false
    const a = newStart < s.start ? s.start : newStart
    const b = newEnd > s.end ? s.end : newEnd
    if (b < a) return false
    setDates(a, b)
    day.value = newDay && newDay >= a && newDay <= b ? newDay : null
    return true
  }

  function setDataset(id) {
    dataset.value = id
    // The harmonized dataset is in MODIS units: the Compare view and the hexagon report follow it (they stay switchable).
    if (datasets.value.find((d) => d.id === id)?.sensor === 'harmonized') {
      compareHarmonized.value = true
      hexHarmonized.value = true
    }
    const info = currentDataset.value
    if (!info?.available || harmonized.value) return // harmonized: the dataset only feeds the points up close
    const outside = !start.value || !end.value || end.value < info.start_date || start.value > info.end_date
    if (outside) lastDays(info)
  }

  function setDates(newStart, newEnd) {
    start.value = newStart
    end.value = newEnd
    day.value = null // a new range starts by showing all of it
  }

  // Time slider: one day of the range, or null for the whole range.
  const setDay = (d) => { day.value = d }

  // A drawn area filters the map and selects all its hexagons, empty ones included,
  // so any of them that burns on a day shown later appears in the panel too.
  // cells === null: too big to select, the area only filters.
  function setArea(geometry, cells) {
    region.value = 'custom'
    zone.value = geometry
    zoneCells.value = cells ?? []
    selectedHexes.value = cells ? [...cells] : []
  }
  const clearZone = () => {
    if (zoneCells.value.length) selectedHexes.value = []
    zone.value = null
    zoneCells.value = []
    region.value = 'view' // clearing a region or an area goes back to following the map
  }
  // The main map moved: [w, s, e, n] on screen (rounded, so small moves don't refetch), or null for the world.
  function setViewBox(box) {
    const g = box && {
      type: 'Polygon',
      coordinates: [[[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]], [box[0], box[1]]]],
    }
    if (JSON.stringify(g) !== JSON.stringify(viewZone.value)) viewZone.value = g
  }
  // A preset region (or null for the world): filters the calendar and the map, and zooms the maps to it.
  function setRegion(id) {
    if (id === 'view') return clearZone()
    const geometry = id ? REGIONS[id] : null
    if (id && !geometry) return
    clearZone()
    region.value = id
    zone.value = geometry
    fitTo.value = { bbox: geometry ? bbox(geometry) : null, n: (fitTo.value?.n ?? 0) + 1 }
  }
  const setBasemap = (type) => { basemap.value = type }
  const setView = (v) => {
    view.value = v
    selectedHexes.value = [] // the panel belongs to the main map
  }

  // Plain click: only this hexagon. Shift/Ctrl/Cmd click: add it, or take it out if already there.
  function selectHex(id, additive = false) {
    const has = selectedHexes.value.includes(id)
    if (!additive) selectedHexes.value = [id]
    else selectedHexes.value = has ? selectedHexes.value.filter((h) => h !== id) : [...selectedHexes.value, id]
  }
  const removeHex = (id) => { selectedHexes.value = selectedHexes.value.filter((h) => h !== id) }
  const clearSelection = () => { selectedHexes.value = [] }
  const setCamera = (center, zoom) => { camera.value = { center, zoom } }
  const setWeatherScope = (scope) => { weatherScope.value = scope }
  const toggleWeatherLayer = (id) => {
    weatherLayers.value = weatherLayers.value.includes(id)
      ? weatherLayers.value.filter((l) => l !== id)
      : [...weatherLayers.value, id]
  }

  return {
    datasets, dataset, start, end, zone, zoneCells, region, viewZone, statsZone, fitTo, error, basemap, day, view, camera, selectedHexes, weatherLayers, weatherScope,
    values, harmonized, harmonizedReady, span, ready, measure, setMeasure,
    currentDataset, queryParams, imagery, compareSides, compareHarmonized, hexHarmonized, imageryDate, isBusy, setBusy,
    loadDatasets, setDataset, setDates, setDay, setRange, setValues, setRegion, setViewBox, setArea, clearZone, setBasemap, setView, setCamera,
    toggleWeatherLayer, setWeatherScope,
    selectHex, removeHex, clearSelection,
  }
})