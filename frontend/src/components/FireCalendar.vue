<script setup>
// Fire calendar: the app's main control. It answers WHEN (and the map next to it answers WHERE), for the whole
// world, a preset region or a drawn area, from 2000 to today on the harmonized series (or one dataset as published).
// Three zoom levels, like any calendar app:
//   Years  — years (rows) x months (columns), plus each year's total and the typical month: which years and which
//            months of the year burn most.
//   Year   — twelve month calendars, one square per day.
//   Month  — one big month calendar, with each day's count.
// A click picks the period the map shows (a month, a year, a day of its month); dragging over days picks a range,
// Shift + click extends it. Arrow keys move between cells, Enter picks. Colors: the value of each cell against the
// biggest of the view (linear or log), or "vs. typical": the same month in the other years (blue below, red above).
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { detectionsCsvUrl, getDaily, getHarmonizedDaily, getHarmonizedMonthly, getMonthly } from '../api'
import { compact, date, datasetName, errorMessage, monthFull, monthLong, monthName, num, period, weekdays } from '../format'
import { REGION_GROUPS } from '../regions'
import { useFiltersStore } from '../stores/filters'
import { useLoadingBar } from '../stores/loading'

const store = useFiltersStore()
const { t } = useI18n()

// ------------------------------------------------------------------ dates ---
const pad = (n) => String(n).padStart(2, '0')
const mKey = (y, m) => `${y}-${pad(m)}`
const first = (y, m) => `${y}-${pad(m)}-01`
const last = (y, m) => new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)
const daysIn = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate()
const addDays = (iso, n) => {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
const weekday = (iso) => (new Date(`${iso}T00:00:00Z`).getUTCDay() + 6) % 7 // Monday = 0
const yearOf = (iso) => +iso.slice(0, 4)
const monthOf = (iso) => +iso.slice(5, 7)
// A month as weeks of 7 (Monday first): null for the blanks before the 1st and after the last day.
function weeks(y, m) {
  const cells = Array(weekday(first(y, m))).fill(null)
  for (let d = 1; d <= daysIn(y, m); d++) cells.push(`${y}-${pad(m)}-${pad(d)}`)
  while (cells.length % 7) cells.push(null)
  return Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7))
}

// ------------------------------------------------------------------- view ---
const level = ref('years') // 'years' | 'year' | 'month'
const focusYear = ref(new Date().getUTCFullYear())
const focusMonth = ref(1)
const measure = ref('detections') // 'detections' | 'frp' | 'anomaly'
const scale = ref('linear') // 'linear' | 'log'
const hovered = ref(null) // key of the cell under the mouse (or with the keyboard focus)
const cursor = ref(null) // key of the cell that takes the Tab focus (roving tabindex)
const grid = ref(null)

// Follow the period picked elsewhere (date inputs, a dataset change): the calendar shows where it is.
watch(() => store.day ?? store.start, (iso) => {
  if (!iso) return
  focusYear.value = yearOf(iso)
  focusMonth.value = monthOf(iso)
}, { immediate: true })

const span = computed(() => store.span)
const inSpan = (iso) => !!span.value && iso >= span.value.start && iso <= span.value.end
const monthInSpan = (y, m) => !!span.value && last(y, m) >= span.value.start && first(y, m) <= span.value.end
const fullMonth = (y, m) => !!span.value && first(y, m) >= span.value.start && last(y, m) <= span.value.end
const years = computed(() => {
  if (!span.value) return []
  const a = yearOf(span.value.start)
  return Array.from({ length: yearOf(span.value.end) - a + 1 }, (_, i) => a + i)
})
const minYear = computed(() => years.value[0])
const maxYear = computed(() => years.value[years.value.length - 1])

// ------------------------------------------------------------------- data ---
const loading = ref(false)
useLoadingBar('stats', loading)
const error = ref(null)
const monthly = ref(new Map()) // 'YYYY-MM' -> row
const daily = ref(new Map()) // 'YYYY-MM-DD' -> row, for focusYear
const dailyCache = new Map()
let monthlyCtl = null
let dailyCtl = null

const source = () => JSON.stringify([store.values, store.harmonized ? null : store.dataset, store.statsZone])

async function loadMonthly() {
  if (!store.ready) return
  monthlyCtl?.abort()
  monthlyCtl = new AbortController()
  const { signal } = monthlyCtl
  loading.value = true
  try {
    const rows = store.harmonized
      ? await getHarmonizedMonthly({ zone: store.statsZone }, signal)
      : await getMonthly(store.dataset, { zone: store.statsZone }, signal)
    monthly.value = new Map(rows.map((r) => [mKey(r.year, r.month), r]))
    error.value = null
  } catch (e) {
    if (e.name !== 'AbortError') { error.value = e; monthly.value = new Map() }
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

async function loadDaily() {
  if (!store.ready || !span.value) return
  const y = focusYear.value
  const key = `${source()}|${y}`
  if (dailyCache.has(key)) { daily.value = dailyCache.get(key); return }
  dailyCtl?.abort()
  dailyCtl = new AbortController()
  const { signal } = dailyCtl
  const params = {
    start: `${y}-01-01` < span.value.start ? span.value.start : `${y}-01-01`,
    end: `${y}-12-31` > span.value.end ? span.value.end : `${y}-12-31`,
    zone: store.statsZone,
  }
  if (params.end < params.start) { daily.value = new Map(); return }
  try {
    const rows = store.harmonized ? await getHarmonizedDaily(params, signal) : await getDaily(store.dataset, params, signal)
    const map = new Map(rows.map((r) => [r.date, r]))
    dailyCache.set(key, map)
    if (dailyCache.size > 40) dailyCache.delete(dailyCache.keys().next().value)
    daily.value = map
  } catch (e) {
    if (e.name !== 'AbortError') daily.value = new Map()
  }
}

watch(() => [store.values, store.ready, store.dataset, store.statsZone], () => { loadMonthly(); loadDaily() }, { immediate: true })
watch(focusYear, loadDaily)

// ----------------------------------------------------------------- values ---
const raw = (row) => (row ? (measure.value === 'frp' ? row.frp_sum ?? 0 : row.detections) : 0)
const count = (row) => (row ? row.detections : 0)

// Typical month m: the mean over the years where the whole month is in the data, without `except` (to compare a
// year against the others). null when there is no other year.
function typical(m, except = null) {
  const ys = years.value.filter((y) => y !== except && fullMonth(y, m))
  if (!ys.length) return null
  return ys.reduce((a, y) => a + (measure.value === 'frp' ? monthly.value.get(mKey(y, m))?.frp_sum ?? 0 : count(monthly.value.get(mKey(y, m)))), 0) / ys.length
}
// Ratio against typical: a month against the same month of the other years; a day against a typical day of its
// month (the other years' month / its days). Always on detections.
function ratio(value, base) {
  if (base === null || base === undefined) return null
  if (base === 0) return value > 0 ? 4 : 1
  return value / base
}
const monthRatio = (y, m) => ratio(count(monthly.value.get(mKey(y, m))), typicalCount(m, y))
const dayRatio = (iso) => {
  const y = yearOf(iso)
  const m = monthOf(iso)
  const base = typicalCount(m, y)
  return ratio(count(daily.value.get(iso)), base === null ? null : base / daysIn(y, m))
}
function typicalCount(m, except) {
  const ys = years.value.filter((y) => y !== except && fullMonth(y, m))
  return ys.length ? ys.reduce((a, y) => a + count(monthly.value.get(mKey(y, m))), 0) / ys.length : null
}

const yearTotal = (y) => {
  let n = 0
  for (let m = 1; m <= 12; m++) n += raw(monthly.value.get(mKey(y, m)))
  return n
}

// Biggest value of the view: the colors (and the bars) are relative to it.
const maxMonth = computed(() => Math.max(1, ...[...monthly.value.values()].map(raw)))
const maxYear_ = computed(() => Math.max(1, ...years.value.map(yearTotal)))
const maxDayYear = computed(() => Math.max(1, ...[...daily.value.values()].map(raw)))
const maxDayMonth = computed(() => {
  const prefix = mKey(focusYear.value, focusMonth.value)
  return Math.max(1, ...[...daily.value.entries()].filter(([k]) => k.startsWith(prefix)).map(([, r]) => raw(r)))
})

// ----------------------------------------------------------------- colors ---
const RAMP = ['#fff7ed', '#fed7aa', '#fdba74', '#f97316', '#dc2626', '#7f1d1d']
const DIVERGING = ['#1d4ed8', '#93c5fd', '#f1f5f9', '#fca5a5', '#b91c1c'] // ¼x .. typical .. 4x
const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16))
function ramp(stops, x) {
  const p = Math.min(1, Math.max(0, x)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(p))
  const [a, b] = [hex(stops[i]), hex(stops[i + 1])]
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * (p - i))).join(',')})`
}
const level01 = (v, max) => (scale.value === 'log' ? Math.log1p(v) / Math.log1p(max) : v / max)
// { background, color } of a cell, or null for "no detections".
function paint(value, max, r) {
  if (measure.value === 'anomaly') {
    if (r === null) return null
    const x = (Math.max(-2, Math.min(2, Math.log2(Math.max(r, 1e-6)))) + 2) / 4
    return { background: ramp(DIVERGING, x), color: Math.abs(x - 0.5) > 0.35 ? '#fff' : '#111827' }
  }
  if (!value) return null
  const x = 0.08 + 0.92 * level01(value, max)
  return { background: ramp(RAMP, x), color: x > 0.62 ? '#fff' : '#111827' }
}

// ------------------------------------------------------------------ texts ---
const units = computed(() => (store.harmonized ? t('cal.modisUnits') : ''))
const fmtValue = (v) => (measure.value === 'frp'
  ? t('common.mw', { v: num(v) })
  : t('common.detections', { n: num(v, store.harmonized ? 1 : 0) }))
const fmtRatio = (r) => {
  if (r === null) return t('cal.noTypical')
  const p = Math.round((r - 1) * 100)
  return p === 0 ? t('cal.asTypical') : t(p > 0 ? 'cal.above' : 'cal.below', { p: num(Math.abs(p)) })
}
function monthText(y, m) {
  const row = monthly.value.get(mKey(y, m))
  if (!monthInSpan(y, m)) return `${monthFull(m)} ${y} · ${t('cal.noData')}`
  return `${monthFull(m)} ${y} · ${fmtValue(raw(row))} · ${fmtRatio(monthRatio(y, m))}`
}
function dayText(iso) {
  if (!inSpan(iso)) return `${date(iso, 'long')} · ${t('cal.noData')}`
  return `${date(iso, 'long')} · ${fmtValue(raw(daily.value.get(iso)))} · ${fmtRatio(dayRatio(iso))}`
}
const readout = computed(() => {
  const k = hovered.value
  if (!k) return null
  return k.length === 7 ? monthText(+k.slice(0, 4), +k.slice(5, 7)) : dayText(k)
})

// -------------------------------------------------------------- selection ---
const inRange = (iso) => !!store.start && iso >= store.start && iso <= store.end
const monthSelected = (y, m) => !!store.start && store.start <= last(y, m) && store.end >= first(y, m)
const drag = ref(null) // { anchor, current } while dragging over days
const preview = (iso) => {
  if (!drag.value) return false
  const [a, b] = [drag.value.anchor, drag.value.current].sort()
  return iso >= a && iso <= b
}

function pickMonth(y, m) {
  if (monthInSpan(y, m)) store.setRange(first(y, m), last(y, m))
}
function pickYear(y) {
  store.setRange(`${y}-01-01`, `${y}-12-31`)
}
// A day: inside a picked period, the time slider jumps to it (again: the whole period); otherwise its month, on that day.
function pickDay(iso) {
  if (!inSpan(iso)) return
  if (store.start !== store.end && inRange(iso)) return store.setDay(store.day === iso ? null : iso)
  store.setRange(first(yearOf(iso), monthOf(iso)), last(yearOf(iso), monthOf(iso)), iso)
}
function pickDays(a, b) {
  const [s, e] = [a, b].sort()
  store.setRange(s, e)
}

// Mouse / touch: press on a day, move over others, release. A press without moving is a click.
const keyAt = (e) => document.elementFromPoint(e.clientX, e.clientY)?.closest?.('[data-k]')?.dataset.k
function onDown(e, iso) {
  if (e.button !== 0 || !inSpan(iso)) return
  if (e.shiftKey && store.start) {
    pickDays(store.day ?? store.start, iso)
    return
  }
  drag.value = { anchor: iso, current: iso }
}
function onMove(e) {
  const k = keyAt(e)
  if (k) hovered.value = k
  if (drag.value && k?.length === 10 && inSpan(k)) drag.value.current = k
}
function onUp() {
  if (!drag.value) return
  const { anchor, current } = drag.value
  drag.value = null
  if (anchor === current) pickDay(anchor)
  else pickDays(anchor, current)
}
// Enter / Space on a day (a click with detail 0): pick it. Mouse clicks are handled by onDown/onUp.
const onDayKey = (e, iso) => { if (e.detail === 0) pickDay(iso) }

// --------------------------------------------------------------- keyboard ---
function move(e) {
  const k = cursor.value ?? hovered.value
  const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -1, ArrowDown: 1 }
  if (!k || !(e.key in steps)) return
  e.preventDefault()
  let next
  if (k.length === 7) { // a month of the years view: left/right a month, up/down a year
    let [y, m] = [+k.slice(0, 4), +k.slice(5, 7)]
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') y += steps[e.key]
    else { m += steps[e.key]; if (m < 1) { m = 12; y-- } else if (m > 12) { m = 1; y++ } }
    if (y < minYear.value || y > maxYear.value) return
    next = mKey(y, m)
  } else { // a day: left/right a day, up/down a week
    next = addDays(k, (e.key === 'ArrowUp' || e.key === 'ArrowDown' ? 7 : 1) * steps[e.key])
    if (yearOf(next) < minYear.value || yearOf(next) > maxYear.value) return
    focusYear.value = yearOf(next)
    if (level.value === 'month') focusMonth.value = monthOf(next)
  }
  cursor.value = next
  hovered.value = next
  nextTick(() => grid.value?.querySelector(`[data-k="${next}"]`)?.focus())
}
const tab = (k) => (k === (cursor.value ?? defaultCursor.value) ? 0 : -1)
const defaultCursor = computed(() => {
  const iso = store.day ?? store.start
  if (!iso) return null
  return level.value === 'years' ? iso.slice(0, 7) : iso
})
watch(level, () => { cursor.value = null })

// ------------------------------------------------------------- navigation ---
function open(lvl, y = focusYear.value, m = focusMonth.value) {
  level.value = lvl
  focusYear.value = y
  focusMonth.value = m
}
function step(n) {
  if (level.value === 'year') {
    focusYear.value = Math.min(maxYear.value, Math.max(minYear.value, focusYear.value + n))
    return
  }
  let [y, m] = [focusYear.value, focusMonth.value + n]
  if (m < 1) { m = 12; y-- } else if (m > 12) { m = 1; y++ }
  if (y < minYear.value || y > maxYear.value) return
  focusYear.value = y
  focusMonth.value = m
}
const canStep = (n) => {
  if (level.value === 'year') return focusYear.value + n >= minYear.value && focusYear.value + n <= maxYear.value
  const m = focusMonth.value + n
  const y = focusYear.value + (m < 1 ? -1 : m > 12 ? 1 : 0)
  return y >= minYear.value && y <= maxYear.value
}
// Anomaly needs other years; with one year of data there is nothing to compare with.
watch(years, (ys) => { if (ys.length < 2 && measure.value === 'anomaly') measure.value = 'detections' })

// ---------------------------------------------------------------- summary ---
// The picked period in numbers, exact from the days when they are loaded, else from whole months.
const summary = computed(() => {
  if (!store.start || !store.end) return null
  const s = store.start
  const e = store.end
  let total = null
  let frp = null
  let peak = null
  if (yearOf(s) === focusYear.value && yearOf(e) === focusYear.value && daily.value.size) {
    total = 0; frp = 0
    for (let d = s; d <= e; d = addDays(d, 1)) {
      const r = daily.value.get(d)
      if (!r) continue
      total += r.detections; frp += r.frp_sum ?? 0
      if (!peak || r.detections > peak.n) peak = { day: d, n: r.detections }
    }
  } else if (s.endsWith('-01') && e === last(yearOf(e), monthOf(e))) {
    total = 0; frp = 0
    for (let d = s; d <= e; d = addDays(last(yearOf(d), monthOf(d)), 1)) {
      const r = monthly.value.get(d.slice(0, 7))
      total += count(r); frp += r?.frp_sum ?? 0
    }
  }
  // How it compares: a single whole month against the same month in the other years (and its rank).
  let compare = null
  const y = yearOf(s)
  const m = monthOf(s)
  if (s === first(y, m) && e === last(y, m) && fullMonth(y, m)) {
    const r = monthRatio(y, m)
    const ranked = years.value.filter((yy) => fullMonth(yy, m))
      .map((yy) => [yy, count(monthly.value.get(mKey(yy, m)))]).sort((a, b) => b[1] - a[1])
    if (r !== null && ranked.length > 1) {
      compare = {
        text: t('cal.compareMonth', { ratio: fmtRatio(r), month: monthFull(m), years: yearsText(ranked.map((x) => x[0]).filter((yy) => yy !== y)) }),
        rank: t('cal.rank', { rank: ranked.findIndex((x) => x[0] === y) + 1, n: ranked.length, month: monthFull(m) }),
        up: r >= 1,
      }
    }
  }
  return { total, frp, peak, compare }
})

// 2019 / 2000–2024 / 2000–2011, 2013 (years compared with)
function yearsText(ys) {
  const sorted = [...ys].sort((a, b) => a - b)
  const runs = []
  for (const y of sorted) {
    const r = runs.at(-1)
    if (r && y === r[1] + 1) r[1] = y
    else runs.push([y, y])
  }
  return runs.map(([a, b]) => (a === b ? String(a) : `${a}–${b}`)).join(', ')
}

// --------------------------------------------------------------- download ---
// Hotspots: original detections of a dataset that covers the period (the chosen one first).
const hotspotsDataset = computed(() => {
  if (!store.start) return null
  const covers = (d) => d?.available && d.start_date <= store.end && d.end_date >= store.start
  if (covers(store.currentDataset)) return store.currentDataset
  return store.datasets.find((d) => d.product === 'best' && covers(d)) ?? null
})
const hotspotsUrl = computed(() => hotspotsDataset.value
  && detectionsCsvUrl(hotspotsDataset.value.id, { start: store.start, end: store.end, zone: store.statsZone }))
const csvBusy = ref(false)
const csvError = ref(null)
// The calendar of the picked period, one row per day, in the calendar's values.
async function downloadCalendar() {
  csvBusy.value = true
  csvError.value = null
  try {
    const params = { start: store.start, end: store.end, zone: store.statsZone }
    const rows = store.harmonized ? await getHarmonizedDaily(params) : await getDaily(store.dataset, params)
    const byDay = new Map(rows.map((r) => [r.date, r]))
    const lines = [['date', 'detections', 'day_detections', 'night_detections', 'frp_sum_mw', 'static_detections', 'values'].join(',')]
    const values = store.harmonized ? 'harmonized_modis_units' : store.dataset
    for (let d = store.start; d <= store.end; d = addDays(d, 1)) {
      const r = byDay.get(d)
      lines.push([d, r?.detections ?? 0, r?.day ?? 0, r?.night ?? 0, r?.frp_sum ?? 0, r?.static_detections ?? 0, values].join(','))
    }
    const url = URL.createObjectURL(new Blob([`${lines.join('\n')}\n`], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `fire_calendar_${values}_${store.start}_${store.end}.csv`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    csvError.value = e
  } finally {
    csvBusy.value = false
  }
}

// ------------------------------------------------------------- lifecycle ---
onMounted(() => window.addEventListener('pointerup', onUp))
onBeforeUnmount(() => {
  window.removeEventListener('pointerup', onUp)
  monthlyCtl?.abort()
  dailyCtl?.abort()
})

const monthNames = computed(() => Array.from({ length: 12 }, (_, i) => monthName(i + 1)))
const regionLabel = computed(() => {
  if (store.region === 'view') return store.statsZone ? t('regions.view') : t('regions.world')
  if (!store.zone) return t('regions.world')
  return store.region && store.region !== 'custom' ? t(`regions.${store.region}`) : t('regions.custom')
})
const title = computed(() => (level.value === 'years'
  ? t('cal.yearsTitle', { from: minYear.value ?? '–', to: maxYear.value ?? '–' })
  : level.value === 'year' ? String(focusYear.value) : monthLong(first(focusYear.value, focusMonth.value))))
const legendMax = computed(() => ({ years: maxMonth.value, year: maxDayYear.value, month: maxDayMonth.value })[level.value])
const legendStyle = computed(() => ({
  background: `linear-gradient(to right, ${(measure.value === 'anomaly' ? DIVERGING : RAMP).join(', ')})`,
}))
</script>

<template>
  <section class="cal" :aria-label="$t('cal.region')">
    <!-- Values and region: what the calendar (and the map) count, and where. -->
    <div class="top">
      <div class="seg values" role="group" :aria-label="$t('cal.values')">
        <button
          type="button" :aria-pressed="store.harmonized" :class="{ on: store.harmonized }"
          :disabled="store.harmonizedReady === false" :title="store.harmonizedReady === false ? $t('harm.notBuilt') : $t('cal.harmonizedHint')"
          @click="store.setValues('harmonized')"
        >{{ $t('harm.harmonized') }}</button>
        <button type="button" :aria-pressed="!store.harmonized" :class="{ on: !store.harmonized }" @click="store.setValues('raw')">
          {{ $t('harm.raw') }}
        </button>
      </div>
      <label class="region">
        <span class="sr-only">{{ $t('regions.label') }}</span>
        <select :value="store.region ?? ''" @change="store.setRegion($event.target.value || null)">
          <option value="view">{{ $t('regions.follow') }}</option>
          <option value="">{{ $t('regions.world') }}</option>
          <option v-if="store.region === 'custom'" value="custom" disabled>{{ $t('regions.custom') }}</option>
          <optgroup v-for="g in REGION_GROUPS" :key="g.id" :label="$t(`regions.groups.${g.id}`)">
            <option v-for="(_, id) in g.regions" :key="id" :value="id">{{ $t(`regions.${id}`) }}</option>
          </optgroup>
        </select>
      </label>
    </div>
    <p class="source">
      {{ store.harmonized ? $t('cal.sourceHarmonized', { from: date(span?.start), to: date(span?.end) }) : $t('cal.sourceRaw', { dataset: datasetName(store.currentDataset) }) }}
      <template v-if="store.harmonizedReady === false"> · {{ $t('harm.notBuilt') }}</template>
    </p>

    <!-- Zoom level and navigation, like a calendar app. -->
    <div class="nav">
      <div class="seg levels" role="group" :aria-label="$t('cal.zoom')">
        <button type="button" :aria-pressed="level === 'years'" :class="{ on: level === 'years' }" @click="open('years')">{{ $t('cal.years') }}</button>
        <button type="button" :aria-pressed="level === 'year'" :class="{ on: level === 'year' }" @click="open('year')">{{ $t('cal.year') }}</button>
        <button type="button" :aria-pressed="level === 'month'" :class="{ on: level === 'month' }" @click="open('month')">{{ $t('cal.month') }}</button>
      </div>
      <div class="pager">
        <button v-if="level !== 'years'" type="button" class="arrow" :disabled="!canStep(-1)" :aria-label="$t('cal.previous')" @click="step(-1)">‹</button>
        <h2 aria-live="polite">{{ title }}</h2>
        <button v-if="level !== 'years'" type="button" class="arrow" :disabled="!canStep(1)" :aria-label="$t('cal.next')" @click="step(1)">›</button>
      </div>
    </div>

    <div class="opts">
      <label>
        {{ $t('cal.color') }}
        <select v-model="measure">
          <option value="detections">{{ $t('cal.measures.detections') }}</option>
          <option value="frp">{{ $t('cal.measures.frp') }}</option>
          <option value="anomaly" :disabled="years.length < 2">{{ $t('cal.measures.anomaly') }}</option>
        </select>
      </label>
      <label v-if="measure !== 'anomaly'">
        {{ $t('cal.scale') }}
        <select v-model="scale">
          <option value="linear">{{ $t('cal.linear') }}</option>
          <option value="log">{{ $t('cal.log') }}</option>
        </select>
      </label>
    </div>

    <p class="readout" aria-hidden="true">
      <template v-if="readout">{{ readout }}</template>
      <span v-else class="muted">{{ level === 'years' ? $t('cal.hintYears') : $t('cal.hintDays') }}</span>
    </p>
    <p v-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>

    <!-- The grids. Each cell is a button: its label says the value; arrows move, Enter picks. -->
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -->
    <div ref="grid" class="grids" :class="{ dragging: drag }" @keydown="move" @pointermove="onMove" @pointerleave="hovered = null">
      <!-- Years x months -->
      <div v-if="level === 'years'" class="years" :class="{ few: years.length <= 8 }" role="group" :aria-label="$t('cal.yearsLabel')">
        <div class="yrow head" aria-hidden="true">
          <span></span>
          <span v-for="n in monthNames" :key="n">{{ n.slice(0, 1) }}</span>
          <span class="tot">{{ $t('cal.total') }}</span>
        </div>
        <div v-for="y in years" :key="y" class="yrow">
          <button type="button" class="ylabel" :aria-label="$t('cal.openYear', { year: y })" @click="pickYear(y); open('year', y)">{{ y }}</button>
          <button
            v-for="m in 12" :key="m" type="button" class="cell"
            :data-k="mKey(y, m)" :tabindex="tab(mKey(y, m))" :disabled="!monthInSpan(y, m)"
            :class="{ off: !monthInSpan(y, m), sel: monthSelected(y, m) }"
            :style="monthInSpan(y, m) ? paint(raw(monthly.get(mKey(y, m))), maxMonth, monthRatio(y, m)) : null"
            :aria-label="monthText(y, m)" :aria-pressed="monthSelected(y, m)"
            @click="pickMonth(y, m)" @dblclick="open('month', y, m)" @focus="hovered = mKey(y, m)"
          ></button>
          <span class="bar" :title="fmtValue(yearTotal(y))" aria-hidden="true">
            <span :style="{ width: `${100 * level01(yearTotal(y), maxYear_)}%` }"></span>
          </span>
        </div>
        <div class="yrow typical" :title="$t('cal.typicalHint')">
          <span class="ylabel static">{{ $t('cal.typical') }}</span>
          <span
            v-for="m in 12" :key="m" class="cell static"
            :style="measure === 'anomaly' ? null : paint(typical(m) ?? 0, maxMonth, null)"
            :aria-label="`${monthFull(m)} · ${typical(m) === null ? $t('cal.noData') : fmtValue(typical(m))}`" role="img"
          ></span>
          <span></span>
        </div>
      </div>

      <!-- Twelve month calendars -->
      <div v-else-if="level === 'year'" class="months">
        <div v-for="m in 12" :key="m" class="mini" role="group" :aria-label="monthFull(m)">
          <button type="button" class="mtitle" :aria-label="$t('cal.openMonth', { month: monthFull(m) })" @click="pickMonth(focusYear, m); open('month', focusYear, m)">
            <span>{{ monthFull(m) }}</span>
            <small>{{ monthInSpan(focusYear, m) ? compact(raw(monthly.get(mKey(focusYear, m)))) : '' }}</small>
          </button>
          <div class="wk head" aria-hidden="true"><span v-for="w in weekdays()" :key="w">{{ w }}</span></div>
          <div v-for="(wk, i) in weeks(focusYear, m)" :key="i" class="wk">
            <template v-for="(iso, j) in wk" :key="j">
              <span v-if="!iso" class="blank"></span>
              <button
                v-else type="button" class="day"
                :data-k="iso" :tabindex="tab(iso)" :disabled="!inSpan(iso)"
                :class="{ off: !inSpan(iso), sel: inRange(iso) || preview(iso), pick: store.day === iso }"
                :style="inSpan(iso) ? paint(raw(daily.get(iso)), maxDayYear, dayRatio(iso)) : null"
                :aria-label="dayText(iso)" :aria-pressed="inRange(iso)"
                @pointerdown="onDown($event, iso)" @click="onDayKey($event, iso)" @focus="hovered = iso"
              ></button>
            </template>
          </div>
        </div>
      </div>

      <!-- One month -->
      <div v-else class="month" role="group" :aria-label="monthLong(first(focusYear, focusMonth))">
        <div class="wk head" aria-hidden="true"><span v-for="w in weekdays()" :key="w">{{ w }}</span></div>
        <div v-for="(wk, i) in weeks(focusYear, focusMonth)" :key="i" class="wk">
          <template v-for="(iso, j) in wk" :key="j">
            <span v-if="!iso" class="blank"></span>
            <button
              v-else type="button" class="big"
              :data-k="iso" :tabindex="tab(iso)" :disabled="!inSpan(iso)"
              :class="{ off: !inSpan(iso), sel: inRange(iso) || preview(iso), pick: store.day === iso }"
              :style="inSpan(iso) ? paint(raw(daily.get(iso)), maxDayMonth, dayRatio(iso)) : null"
              :aria-label="dayText(iso)" :aria-pressed="inRange(iso)"
              @pointerdown="onDown($event, iso)" @click="onDayKey($event, iso)" @focus="hovered = iso"
            >
              <span class="dn">{{ +iso.slice(8) }}</span>
              <span class="dv">{{ inSpan(iso) && daily.get(iso) ? compact(raw(daily.get(iso))) : '' }}</span>
            </button>
          </template>
        </div>
      </div>
    </div>

    <!-- Legend -->
    <div class="legend" aria-hidden="true">
      <span>{{ measure === 'anomaly' ? '¼×' : '0' }}</span>
      <span class="ramp" :style="legendStyle"></span>
      <span>{{ measure === 'anomaly' ? '4×' : compact(legendMax) }}</span>
      <span class="sw none"></span>{{ $t('cal.none') }}
      <span class="sw off"></span>{{ $t('cal.noData') }}
    </div>
    <p class="legend-note">
      {{ measure === 'anomaly' ? $t('cal.anomalyHint') : $t('cal.relativeHint') }}
    </p>

    <!-- The picked period: what the map shows. -->
    <div v-if="store.start" class="summary">
      <div class="sum-head">
        <span class="sum-label">{{ $t('cal.onMap') }}</span>
        <b>{{ period(store.start, store.end) }}</b>
        <span class="muted"> · {{ regionLabel }}</span>
      </div>
      <p v-if="summary?.total !== null" class="sum-line">
        {{ $t('common.detections', { n: num(summary.total, store.harmonized ? 1 : 0) }) }}{{ units }}
        · {{ $t('common.mw', { v: num(summary.frp) }) }}
        <template v-if="summary.peak && store.start !== store.end"> · {{ $t('cal.peak', { day: date(summary.peak.day) }) }}</template>
      </p>
      <p v-if="summary?.compare" class="sum-line compare" :class="summary.compare.up ? 'up' : 'down'">
        {{ summary.compare.text }} <span class="muted">{{ summary.compare.rank }}</span>
      </p>
      <div class="downloads">
        <button type="button" :disabled="csvBusy || !store.ready" @click="downloadCalendar">
          <span aria-hidden="true">⤓</span> {{ $t('cal.csvCalendar') }}
        </button>
        <a v-if="hotspotsUrl" :href="hotspotsUrl" download :title="$t('cal.csvHotspotsHint', { dataset: datasetName(hotspotsDataset) })">
          <span aria-hidden="true">⤓</span> {{ $t('cal.csvHotspots') }}
        </a>
      </div>
      <p v-if="csvError" class="error" role="alert">{{ errorMessage(csvError) }}</p>
    </div>
  </section>
</template>

<style scoped>
.cal { display: flex; flex-direction: column; gap: 8px; }
.top { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
.seg { display: inline-flex; padding: 2px; gap: 2px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
.seg button {
  font: inherit; font-size: 0.8125rem; padding: 4px 10px; border: 0; border-radius: 6px; background: none;
  color: var(--muted); cursor: pointer;
}
.seg button.on { background: var(--accent); color: var(--on-accent); font-weight: 600; }
.seg button:disabled { opacity: 0.45; cursor: not-allowed; }
.region { display: inline-flex; align-items: center; gap: 6px; margin-left: 4px; min-width: 0; flex: 1; justify-content: flex-end; }
.region select, .opts select {
  font: inherit; font-size: 0.8125rem; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; max-width: 100%;
}
.source { margin: -2px 0 0; font-size: 0.6875rem; color: var(--muted); }
.nav { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.pager { display: flex; align-items: center; gap: 4px; }
.pager h2 { margin: 0; font-size: 1.125rem; min-width: 7rem; text-align: center; }
.arrow {
  width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--border); background: var(--bg);
  color: var(--text); font-size: 1.125rem; line-height: 1; cursor: pointer;
}
.arrow:disabled { opacity: 0.35; cursor: default; }
.opts { display: flex; gap: 10px; flex-wrap: wrap; }
.opts label { display: flex; align-items: center; gap: 6px; font-size: 0.75rem; color: var(--muted); }
.readout { margin: 0; min-height: 2.4em; font-size: 0.75rem; line-height: 1.2; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.error { margin: 0; color: var(--danger); font-size: 0.75rem; }
.grids { user-select: none; touch-action: none; }

/* Cells: color = value; outline = the period on the map; dark ring = the slider's day. */
.cell, .day, .big {
  position: relative; border: 0; padding: 0; margin: 0; font: inherit; cursor: pointer;
  background: var(--surface-3); color: var(--text); border-radius: 3px;
}
.cell.off, .day.off, .big.off {
  cursor: default; color: var(--muted);
  background: repeating-linear-gradient(135deg, transparent 0 3px, var(--border) 3px 4px);
}
.cell.sel, .day.sel, .big.sel { box-shadow: inset 0 0 0 2px var(--selected); }
.day.pick, .big.pick { box-shadow: 0 0 0 2px var(--panel), 0 0 0 4px var(--selected); z-index: 1; }
.cell:not(.off):hover, .day:not(.off):hover, .big:not(.off):hover { outline: 2px solid var(--text); outline-offset: -1px; z-index: 2; }

/* Years x months */
.years { display: flex; flex-direction: column; gap: 2px; }
.yrow { display: grid; grid-template-columns: 3.6rem repeat(12, 1fr) 3.2rem; gap: 2px; align-items: stretch; }
.yrow.head span { font-size: 0.625rem; color: var(--muted); text-align: center; }
.yrow .cell { height: 1.05rem; }
.years.few .yrow .cell { height: 2rem; } /* a few years of data: bigger cells */
.ylabel {
  font: inherit; font-size: 0.6875rem; text-align: right; padding: 0 4px 0 0; border: 0; background: none;
  color: var(--muted); cursor: pointer; font-variant-numeric: tabular-nums;
}
.ylabel:hover { color: var(--accent); text-decoration: underline; }
.ylabel.static { cursor: default; text-decoration: none; font-weight: 600; }
.bar { display: flex; align-items: center; padding-left: 3px; }
.bar span { display: block; height: 0.55rem; border-radius: 0 3px 3px 0; background: #ea580c; min-width: 1px; }
.tot { text-align: left !important; padding-left: 3px; }
.typical { margin-top: 4px; padding-top: 4px; border-top: 1px dashed var(--border); }
.cell.static { cursor: default; height: 1.05rem; border-radius: 3px; background: var(--surface-3); }

/* Twelve months */
.months { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px 8px; }
.mini { display: flex; flex-direction: column; gap: 1px; }
.mtitle {
  display: flex; justify-content: space-between; align-items: baseline; font: inherit; font-size: 0.75rem; font-weight: 600;
  border: 0; background: none; padding: 0 1px 2px; color: var(--text); cursor: pointer; text-align: left;
}
.mtitle small { font-weight: 400; color: var(--muted); font-size: 0.625rem; }
.mtitle:hover span { color: var(--accent); text-decoration: underline; }
.wk { display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; }
.wk.head span { font-size: 0.5625rem; color: var(--muted); text-align: center; }
.day { aspect-ratio: 1; border-radius: 2px; }
.blank { aspect-ratio: 1; }

/* One month */
.month { display: flex; flex-direction: column; gap: 3px; }
.month .wk { gap: 3px; }
.month .wk.head span { font-size: 0.6875rem; }
.big { aspect-ratio: 1 / 0.9; display: flex; flex-direction: column; justify-content: space-between; padding: 3px 4px; border-radius: 5px; }
.month .blank { aspect-ratio: 1 / 0.9; }
.dn { font-size: 0.75rem; font-weight: 600; align-self: flex-start; }
.dv { font-size: 0.6875rem; align-self: flex-end; font-variant-numeric: tabular-nums; }

/* Legend */
.legend { display: flex; align-items: center; gap: 6px; font-size: 0.6875rem; color: var(--muted); flex-wrap: wrap; }
.ramp { width: 7rem; height: 0.6rem; border-radius: 3px; }
.sw { width: 0.75rem; height: 0.75rem; border-radius: 2px; margin-left: 6px; }
.sw.none { background: var(--surface-3); }
.sw.off { background: repeating-linear-gradient(135deg, transparent 0 2px, var(--border) 2px 3px); border: 1px solid var(--border); }
.legend-note { margin: -4px 0 0; font-size: 0.6875rem; color: var(--muted); }

/* Summary */
.summary {
  display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; border-radius: 8px;
  background: var(--surface-2); border: 1px solid var(--border);
}
.sum-label { display: block; font-size: 0.625rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.sum-head b { font-size: 0.9375rem; }
.sum-line { margin: 0; font-size: 0.75rem; }
.compare.up { color: #b91c1c; }
.compare.down { color: #1d4ed8; }
.downloads { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 2px; }
.downloads button, .downloads a {
  font: inherit; font-size: 0.75rem; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--bg); color: var(--text); cursor: pointer; text-decoration: none;
}
.downloads button:disabled { opacity: 0.5; cursor: default; }
</style>
