<script setup>
// Time slider under the map: walk the selected range (From → To) one day at a time.
// The bars are detections per day (same dataset and zone as the map), so the busy
// days stand out. Moving the handle shows only that day on the map; "All days"
// goes back to the whole range. ▶ plays the days in a loop.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getDaily } from '../api'
import { useFiltersStore } from '../stores/filters'

const PLAY_MS = 1200 // per day: leaves time for the map to load each day

const store = useFiltersStore()
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

const fmt = (n) => (n ?? 0).toLocaleString('en-US')
const pretty = (iso) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US', {
    weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  })

const title = computed(() => (store.day ? pretty(store.day) : `All days · ${days.value.length} days`))
const subtitle = computed(() =>
  `${fmt(store.day ? counts.value[store.day] : Object.values(counts.value).reduce((a, b) => a + b, 0))} detections`,
)
const label = computed(() => `${title.value} · ${subtitle.value}`) // for screen readers

async function loadCounts() {
  counts.value = {}
  if (!store.dataset || !store.currentDataset?.available || !days.value.length) return
  controller?.abort()
  controller = new AbortController()
  try {
    // The whole range (not just the slider's day), so every bar has its height.
    const rows = await getDaily(
      store.dataset,
      { start: store.start, end: store.end, zone: store.zone },
      controller.signal,
    )
    counts.value = Object.fromEntries(rows.map((r) => [r.date, r.detections]))
  } catch (e) {
    if (e.name !== 'AbortError') counts.value = {}
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

function play() {
  if (!days.value.length) return
  playing.value = true
  if (index.value < 0) select(0)
  timer = setInterval(step, PLAY_MS)
}

function stop() {
  playing.value = false
  clearInterval(timer)
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
  <section class="timeline" aria-label="Time slider">
    <button class="play" :aria-label="playing ? 'Pause' : 'Play days'" :disabled="!days.length" @click="toggle">
      {{ playing ? '❚❚' : '▶' }}
    </button>

    <div class="info">
      <div class="day">{{ title }}</div>
      <div class="sub">
        {{ subtitle }}
        <button v-if="store.day" class="link" @click="showAll">· All days</button>
      </div>
    </div>

    <div class="track">
      <svg class="bars" :viewBox="`0 0 ${Math.max(days.length, 1)} 100`" preserveAspectRatio="none" aria-hidden="true">
        <rect
          v-for="(d, i) in days" :key="d"
          :x="i + 0.1" :y="100 - barHeight(d)" width="0.8" :height="barHeight(d)"
          :class="{ on: d === store.day }"
          @click="stop(); select(i)"
        >
          <title>{{ pretty(d) }} · {{ fmt(counts[d]) }} detections</title>
        </rect>
      </svg>
      <input
        type="range" min="0" :max="Math.max(days.length - 1, 0)" step="1"
        :value="Math.max(index, 0)" :disabled="!days.length"
        :aria-valuetext="label" aria-label="Day" :class="{ idle: !store.day }"
        @input="stop(); select(+$event.target.value)"
      />
      <div class="ends">
        <span>{{ days[0] }}</span>
        <span>{{ days[days.length - 1] }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline {
  display: grid; grid-template-columns: auto 190px 1fr; gap: 14px; align-items: center;
  padding: 8px 16px; background: var(--panel); border-top: 1px solid var(--border);
}
.play {
  width: 36px; height: 36px; border-radius: 50%; border: none; cursor: pointer;
  background: var(--accent); color: #fff; font-size: 14px;
}
.play:disabled { opacity: 0.4; cursor: default; }
.info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.day { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { font-size: 12px; color: var(--muted); white-space: nowrap; }
.link { background: none; border: none; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
.track { position: relative; min-width: 0; }
.bars { display: block; width: 100%; height: 34px; }
.bars rect { fill: #fdba74; cursor: pointer; }
.bars rect:hover { fill: #f97316; }
.bars rect.on { fill: #b91c1c; }
input[type='range'] { display: block; width: 100%; margin: 2px 0 0; accent-color: var(--accent); }
input[type='range'].idle { opacity: 0.45; } /* "All days": the handle isn't selecting anything yet */
.ends { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); }
@media (max-width: 760px) {
  .timeline { grid-template-columns: auto 1fr; }
  .track { grid-column: 1 / -1; }
}
</style>
