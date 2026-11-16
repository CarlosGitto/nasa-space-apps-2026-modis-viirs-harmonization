<script setup>
// Time slider under the map: walk the selected range (From → To) one day at a time.
// The bars are detections per day (same dataset and zone as the map), so the busy
// days stand out; they take the calendar's color (store.measure): the fire ramp by hotspots or
// FRP, or, for anomalies, each day against a typical day of its month in the other years. Moving the handle shows only that day on the map; "All days"
// goes back to the whole range. ▶ plays the days in a loop.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getDaily, getHarmonizedDaily, getHarmonizedMonthly, getMonthly } from '../api'
import { date, num } from '../format'
import { ANOMALY_RAMP, FIRE_RAMP, anomalyLevel, rampColor } from '../mapShared'
import { useFiltersStore } from '../stores/filters'
import { useLoadingBar } from '../stores/loading'

const HOLD_MS = 900 // each day stays on screen at least this long once fully drawn
const MAX_WAIT_MS = 10_000 // never wait longer than this for a slow map
const TICK_MS = 150

const store = useFiltersStore()
const loading = ref(false)
useLoadingBar('stats', loading)
const { t } = useI18n()
const counts = ref({}) // 'YYYY-MM-DD' -> detections
const frp = ref({}) // 'YYYY-MM-DD' -> FRP sum (MW)
const monthly = ref(new Map()) // 'YYYY-MM' -> detections, for the anomalies' typical day
const playing = ref(false)
let timer = null
let controller = null

const addDays = (iso, n) => {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

// Every day of the selected range, in order.
const days = computed(() => {
  if (!store.start || !store.end || store.end < store.start) return []
  const out = []
  for (let d = store.start; d <= store.end; d = addDays(d, 1)) out.push(d)
  return out
})

const index = computed(() => (store.day ? days.value.indexOf(store.day) : -1))
// Height and color follow the calendar's measure: FRP for fire power, detections otherwise.
const valueOf = (d) => (store.measure === 'frp' ? frp.value[d] : counts.value[d]) ?? 0
const max = computed(() => Math.max(1, ...days.value.map(valueOf)))
// sqrt scale: quiet days stay visible next to very busy ones.
const share = (d) => Math.sqrt(valueOf(d) / max.value)
const barHeight = (d) => (valueOf(d) ? Math.max(4, 100 * share(d)) : 0)

// Anomalies, as the calendar: a day against a typical day of its month = the mean of that month over the other
// years whose month is whole in the data, divided by its days. null when there is no other year.
const pad = (n) => String(n).padStart(2, '0')
const daysIn = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate()
function dayRatio(d) {
  const span = store.span
  if (!span) return null
  const [y, m] = [+d.slice(0, 4), +d.slice(5, 7)]
  const whole = (yy) => `${yy}-${pad(m)}-01` >= span.start && `${yy}-${pad(m)}-${pad(daysIn(yy, m))}` <= span.end
  const years = []
  for (let yy = +span.start.slice(0, 4); yy <= +span.end.slice(0, 4); yy++) if (yy !== y && whole(yy)) years.push(yy)
  if (!years.length) return null
  const base = years.reduce((a, yy) => a + (monthly.value.get(`${yy}-${pad(m)}`) ?? 0), 0) / years.length / daysIn(y, m)
  const n = counts.value[d] ?? 0
  return base === 0 ? (n > 0 ? 4 : 1) : n / base
}
const NEUTRAL = ANOMALY_RAMP[(ANOMALY_RAMP.length - 1) / 2]
function barColor(d) {
  if (store.measure !== 'anomaly') return rampColor(FIRE_RAMP, 0.15 + 0.85 * share(d))
  const r = dayRatio(d)
  return r === null ? NEUTRAL : rampColor(ANOMALY_RAMP, anomalyLevel(r))
}
function barTitle(d) {
  const base = store.measure === 'frp'
    ? t('slider.barFrp', { date: pretty(d), v: fmt(frp.value[d]) })
    : t('slider.bar', { date: pretty(d), n: fmt(counts.value[d]) })
  if (store.measure !== 'anomaly') return base
  const r = dayRatio(d)
  if (r === null) return `${base} · ${t('cal.noTypical')}`
  const p = Math.round((r - 1) * 100)
  return `${base} · ${p === 0 ? t('cal.asTypical') : t(p > 0 ? 'cal.above' : 'cal.below', { p: num(Math.abs(p)) })}`
}

const fmt = (n) => num(n ?? 0)
const pretty = (iso) => date(iso, 'long')

const title = computed(() => (store.day ? pretty(store.day) : t('slider.allDays', { n: num(days.value.length) })))
const subtitle = computed(() =>
  t('common.detections', { n: fmt(store.day ? counts.value[store.day] : Object.values(counts.value).reduce((a, b) => a + b, 0)) }),
)
const label = computed(() => `${title.value} · ${subtitle.value}`) // for screen readers

async function loadCounts() {
  counts.value = {}
  frp.value = {}
  if (!store.ready || !days.value.length) return
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  loading.value = true
  try {
    // The whole range (not just the slider's day), so every bar has its height.
    const params = { start: store.start, end: store.end, zone: store.statsZone }
    const rows = store.harmonized ? await getHarmonizedDaily(params, signal) : await getDaily(store.dataset, params, signal)
    counts.value = Object.fromEntries(rows.map((r) => [r.date, r.detections]))
    frp.value = Object.fromEntries(rows.map((r) => [r.date, r.frp_sum ?? 0]))
  } catch (e) {
    if (e.name !== 'AbortError') counts.value = {}
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

function select(i) {
  store.setDay(days.value[i] ?? null)
}

function step() {
  const n = days.value.length
  if (!n) return stop()
  select(index.value < 0 || index.value >= n - 1 ? 0 : index.value + 1)
}

// Playing waits for the map: the next day comes only after the current one is fully
// drawn (data and imagery) and has been visible HOLD_MS. A fixed interval would skip
// ahead of a slow map, and the picture would no longer match the day on the slider.
let shownAt = 0 // when the current day was selected
let readyAt = null // when the map finished drawing it

function tick() {
  if (!playing.value) return
  const now = Date.now()
  // Give the maps a moment to start loading before trusting "not busy".
  if (store.isBusy || now - shownAt < 300) readyAt = null
  else readyAt ??= now
  if ((readyAt && now - readyAt >= HOLD_MS) || now - shownAt >= MAX_WAIT_MS) {
    step()
    shownAt = now
    readyAt = null
  }
  timer = setTimeout(tick, TICK_MS)
}

function play() {
  if (!days.value.length) return
  playing.value = true
  if (index.value < 0) select(0)
  shownAt = Date.now()
  readyAt = null
  timer = setTimeout(tick, TICK_MS)
}

function stop() {
  playing.value = false
  clearTimeout(timer)
  timer = null
}

const toggle = () => (playing.value ? stop() : play())

function showAll() {
  stop()
  store.setDay(null)
}

watch(() => [store.values, store.ready, store.dataset, store.start, store.end, store.statsZone], loadCounts, { immediate: true })

// The months of every year (same values and zone), for the anomalies' typical day: asked only while they're shown.
let monthlyCtl = null
async function loadMonthly() {
  monthly.value = new Map()
  if (store.measure !== 'anomaly' || !store.ready) return
  monthlyCtl?.abort()
  monthlyCtl = new AbortController()
  const { signal } = monthlyCtl
  try {
    const params = { zone: store.statsZone }
    const rows = store.harmonized ? await getHarmonizedMonthly(params, signal) : await getMonthly(store.dataset, params, signal)
    monthly.value = new Map(rows.map((r) => [`${r.year}-${pad(r.month)}`, r.detections]))
  } catch (e) {
    if (e.name !== 'AbortError') monthly.value = new Map()
  }
}
watch(() => [store.measure, store.values, store.ready, store.dataset, store.statsZone], loadMonthly, { immediate: true })
// A new range or dataset: stop playing (the day was reset by the store).
watch(() => [store.dataset, store.start, store.end], stop)
// The split view has no time slider (each side is a whole period): stop, keep the day for when the map comes back.
watch(() => store.view, (view) => { if (view !== 'map') stop() })
onBeforeUnmount(() => {
  stop()
  controller?.abort()
  monthlyCtl?.abort()
})
</script>

<template>
  <section class="timeline" :aria-label="$t('slider.region')">
    <button class="play" :aria-label="(playing ? $t('slider.pause') : $t('slider.play'))" :disabled="!days.length" @click="toggle">
      <span aria-hidden="true">{{ playing ? '❚❚' : '▶' }}</span>
    </button>

    <div class="info" aria-live="polite">
      <div class="day">{{ title }}</div>
      <div class="sub">
        {{ subtitle }}
        <button v-if="store.day" class="link" @click="showAll">{{ $t('slider.showAll') }}</button>
      </div>
    </div>

    <div class="track">
      <svg class="bars" :viewBox="`0 0 ${Math.max(days.length, 1)} 100`" preserveAspectRatio="none" aria-hidden="true">
        <!-- Clicking a bar is a mouse shortcut; the slider below does the same from the keyboard. -->
        <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -->
        <rect
          v-for="(d, i) in days" :key="d"
          :x="i + 0.1" :y="100 - barHeight(d)" width="0.8" :height="barHeight(d)"
          :class="{ on: d === store.day, dim: store.day && d !== store.day }" :style="{ fill: barColor(d) }"
          @click="stop(); select(i)"
        >
          <title>{{ barTitle(d) }}</title>
        </rect>
      </svg>
      <input
        type="range" min="0" :max="Math.max(days.length - 1, 0)" step="1"
        :value="Math.max(index, 0)" :disabled="!days.length"
        :aria-valuetext="label" :aria-label="$t('slider.day')" :class="{ idle: !store.day }"
        @input="stop(); select(+$event.target.value)"
      />
      <div class="ends">
        <span>{{ date(days[0]) }}</span>
        <span>{{ date(days[days.length - 1]) }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline {
  display: grid; grid-template-columns: auto 11.875rem 1fr; gap: 14px; align-items: center;
  padding: 8px 16px; background: var(--panel); border-top: 1px solid var(--border);
}
.play {
  width: 36px; height: 36px; border-radius: 50%; border: none; cursor: pointer;
  background: var(--accent); color: var(--on-accent); font-size: 0.875rem;
}
.play:disabled { opacity: 0.4; cursor: default; }
.info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.day { font-size: 0.8125rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { font-size: 0.75rem; color: var(--muted); white-space: nowrap; }
.link { background: none; border: none; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
.track { position: relative; min-width: 0; }
.bars { display: block; width: 100%; height: 34px; }
/* Colored like the calendar (inline fill). With a day picked, the others step back so it stands out. */
.bars rect { cursor: pointer; transition: opacity 0.15s; }
.bars rect.dim { opacity: 0.35; }
.bars rect:hover { opacity: 1; }
input[type='range'] { display: block; width: 100%; margin: 2px 0 0; accent-color: var(--accent); }
input[type='range'].idle { opacity: 0.45; } /* "All days": the handle isn't selecting anything yet */
.ends { display: flex; justify-content: space-between; white-space: nowrap; font-size: 0.6875rem; color: var(--muted); }
/* Narrow map area (small screen, side panel open or bigger text): the track gets its own row. */
@container map-area (max-width: 44rem) {
  .timeline { grid-template-columns: auto 1fr; }
  .track { grid-column: 1 / -1; }
}
</style>
