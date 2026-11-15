// The shared "coordinator": which dataset, which dates, which zone and which basemap.
// Components watch `dataset` / `queryParams` and refetch when they change.
// zone === null means the whole world; day === null means every day of start..end.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listDatasets } from '../api'
import { pickLayer } from '../gibs'

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
  const error = ref(null)
  const basemap = ref('vector') // 'vector' (OpenFreeMap) or an image type: 'truecolor' | 'fire'
  const day = ref(null) // one day within start..end picked on the time slider, or null = the whole range
  const view = ref('map') // 'map' | 'compare' (MODIS | VIIRS curtain)
  const camera = ref({ center: [0, 20], zoom: 1.5 }) // shared by both views
  const selectedHexes = ref([]) // H3 res-6 ids open in the hexagon panel, in click order
  // Maps still loading a change (data and imagery): the time slider waits for them before
  // showing the next day, so the picture always matches the day on the slider.
  const busy = ref({}) // view -> true while loading

  const isBusy = computed(() => Object.values(busy.value).some(Boolean))
  const setBusy = (key, value) => { busy.value = { ...busy.value, [key]: value } }

  const currentDataset = computed(() => datasets.value.find((d) => d.id === dataset.value) ?? null)

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
      error.value = `Could not connect to the backend: ${e.message}`
      return
    }
    const available = datasets.value.filter((d) => d.available)
    const pick = available.find((d) => d.id === PREFERRED_DATASET) ?? available[0]
    if (pick) {
      dataset.value = pick.id
      lastDays(pick)
    }
  }

  function setDataset(id) {
    dataset.value = id
    const info = currentDataset.value
    if (!info?.available) return
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

  // Burn calendar click: show that whole month.
  function setMonth(year, month) {
    const first = `${year}-${String(month).padStart(2, '0')}-01`
    const last = isoDate(new Date(Date.UTC(year, month, 0)))
    setDates(first, last)
  }

  // A drawn area filters the map and selects all its hexagons, empty ones included,
  // so any of them that burns on a day shown later appears in the panel too.
  // cells === null: too big to select, the area only filters.
  function setArea(geometry, cells) {
    zone.value = geometry
    zoneCells.value = cells ?? []
    selectedHexes.value = cells ? [...cells] : []
  }
  const clearZone = () => {
    if (zoneCells.value.length) selectedHexes.value = []
    zone.value = null
    zoneCells.value = []
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

  return {
    datasets, dataset, start, end, zone, zoneCells, error, basemap, day, view, camera, selectedHexes,
    currentDataset, queryParams, imagery, compareSides, compareHarmonized, hexHarmonized, imageryDate, isBusy, setBusy,
    loadDatasets, setDataset, setDates, setDay, setMonth, setArea, clearZone, setBasemap, setView, setCamera,
    selectHex, removeHex, clearSelection,
  }
})