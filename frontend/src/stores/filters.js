// The shared "coordinator": which dataset, which dates, which zone and which basemap.
// Components watch `dataset` / `queryParams` and refetch when they change.
// zone === null means the whole world; day === null means every day of start..end.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listDatasets } from '../api'
import { pickLayer } from '../gibs'

const DEFAULT_DAYS = 7
const PREFERRED_DATASET = 'viirs_nrt'

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
  const error = ref(null)
  const basemap = ref('vector') // 'vector' (OpenFreeMap) or an image type: 'truecolor' | 'fire'
  const day = ref(null) // one day within start..end picked on the time slider, or null = the whole range

  const currentDataset = computed(() => datasets.value.find((d) => d.id === dataset.value) ?? null)

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

  const setZone = (geometry) => { zone.value = geometry }
  const clearZone = () => { zone.value = null }
  const setBasemap = (type) => { basemap.value = type }

  return {
    datasets, dataset, start, end, zone, error, basemap, day,
    currentDataset, queryParams, imagery,
    loadDatasets, setDataset, setDates, setDay, setMonth, setZone, clearZone, setBasemap,
  }
})