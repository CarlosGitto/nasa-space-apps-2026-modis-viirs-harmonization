<script setup>
// Time slider under the map: walk the selected range (From → To) one day at a time.
// The bars are detections per day (same dataset and zone as the map), so the busy
// days stand out. Moving the handle shows only that day on the map; "All days"
// goes back to the whole range. ▶ plays the days in a loop.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getDaily } from '../api'
import { date, num } from '../format'
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
const max = computed(() => Math.max(1, ...days.value.map((d) => counts.value[d] ?? 0)))
// sqrt scale: quiet days stay visible next to very busy ones.
const barHeight = (d) => {
  const n = counts.value[d] ?? 0
  return n ? Math.max(4, 100 * Math.sqrt(n / max.value)) : 0
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
  if (!store.dataset || !store.currentDataset?.available || !days.value.length) return
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  loading.value = true
  try {
    // The whole range (not just the slider's day), so every bar has its height.
    const rows = await getDaily(store.dataset, { start: store.start, end: store.end, zone: store.zone }, signal)
    counts.value = Object.fromEntries(rows.map((r) => [r.date, r.detections]))
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

watch(() => [store.dataset, store.start, store.end, store.zone], loadCounts, { immediate: true })
// A new range or dataset: stop playing (the day was reset by the store).
watch(() => [store.dataset, store.start, store.end], stop)
onBeforeUnmount(() => {
  stop()
  controller?.abort()
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
          :class="{ on: d === store.day }"
          @click="stop(); select(i)"
        >
          <title>{{ $t('slider.bar', { date: pretty(d), n: fmt(counts[d]) }) }}</title>
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
.bars rect { fill: #fdba74; cursor: pointer; }
.bars rect:hover { fill: #f97316; }
.bars rect.on { fill: #b91c1c; }
input[type='range'] { display: block; width: 100%; margin: 2px 0 0; accent-color: var(--accent); }
input[type='range'].idle { opacity: 0.45; } /* "All days": the handle isn't selecting anything yet */
.ends { display: flex; justify-content: space-between; white-space: nowrap; font-size: 0.6875rem; color: var(--muted); }
/* Narrow map area (small screen, side panel open or bigger text): the track gets its own row. */
@container map-area (max-width: 44rem) {
  .timeline { grid-template-columns: auto 1fr; }
  .track { grid-column: 1 / -1; }
}
</style>
