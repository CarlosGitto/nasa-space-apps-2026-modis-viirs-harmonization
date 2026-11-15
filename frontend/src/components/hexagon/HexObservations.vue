<script setup>
// Observaciones: every detection of the selected hexagons, like FIRMS' table.
// Respects the chosen dataset and dates (or the time slider's day). One request for
// the whole selection (a drawn area can hold thousands of hexagons).
// The values are shown as FIRMS gives them (ISO dates, dot decimals): a technical
// table people copy from; the labels around it are translated.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getSelectionDetections } from '../../api'
import { date, errorMessage } from '../../format'
import { useFiltersStore } from '../../stores/filters'
import { useLoadingBar } from '../../stores/loading'
import { num } from './charts'

const LIMIT = 5000

const props = defineProps({ hexes: { type: Array, required: true } })
const store = useFiltersStore()

const rows = ref([])
const truncated = ref(false)
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
useLoadingBar('panel', loading)
const satellite = ref('all')
const localTime = ref(false)
const filter = ref('')
const sort = ref({ key: 'time', desc: true })
let controller = null

const supported = computed(() => !store.dataset?.startsWith('harmonized'))

async function load() {
  controller?.abort()
  if (!supported.value || !props.hexes.length) {
    rows.value = []
    return
  }
  controller = new AbortController()
  const { signal } = controller
  const body = {
    h3: props.hexes, dataset: store.dataset, start: store.queryParams.start, end: store.queryParams.end, limit: LIMIT,
  }
  loading.value = true
  error.value = null
  try {
    const result = await getSelectionDetections(body, signal)
    rows.value = result.rows
    truncated.value = result.truncated
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e
    rows.value = []
  }
  loading.value = false
}
watch(() => [props.hexes, store.dataset, store.queryParams.start, store.queryParams.end], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

// Sub-tabs: all, then one per satellite (like FIRMS).
const satellites = computed(() => {
  const counts = {}
  for (const r of rows.value) counts[r.satellite ?? '–'] = (counts[r.satellite ?? '–'] ?? 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})
watch(satellites, (list) => { if (!list.some(([s]) => s === satellite.value)) satellite.value = 'all' })

// Local solar time = UTC + longitude / 15 h (the fire's own time of day).
function timeOf(r) {
  const utc = new Date(`${r.acq_datetime_utc}Z`)
  const t = localTime.value ? new Date(utc.getTime() + (r.longitude / 15) * 3_600_000) : utc
  return t.toISOString().slice(0, 16).replace('T', ' ')
}

// Column labels: obs.cols.<key> in the locales ('time' depends on UTC / solar).
const COLUMNS = [
  { key: 'time', value: (r) => r.acq_datetime_utc },
  { key: 'satellite', value: (r) => r.satellite ?? '' },
  { key: 'product', value: (r) => r.product },
  { key: 'latitude', value: (r) => r.latitude },
  { key: 'longitude', value: (r) => r.longitude },
  { key: 'frp', value: (r) => r.frp ?? -1 },
  { key: 'confidence', value: (r) => r.confidence_raw ?? '' },
  { key: 'daynight', value: (r) => r.daynight ?? '' },
  { key: 'brightness', value: (r) => r.brightness ?? -1 },
  { key: 'brightness_aux', value: (r) => r.brightness_aux ?? -1 },
  { key: 'scan', value: (r) => r.scan ?? -1 },
  { key: 'track', value: (r) => r.track ?? -1 },
  { key: 'version', value: (r) => r.version ?? '' },
]

const visible = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const col = COLUMNS.find((c) => c.key === sort.value.key)
  const dir = sort.value.desc ? -1 : 1
  return rows.value
    .filter((r) => satellite.value === 'all' || (r.satellite ?? '–') === satellite.value)
    .filter((r) => !q || [timeOf(r), r.satellite, r.product, r.confidence_raw, r.confidence_level, r.version, r.h3, r.frp]
      .some((v) => String(v ?? '').toLowerCase().includes(q)))
    .sort((a, b) => (col.value(a) > col.value(b) ? dir : col.value(a) < col.value(b) ? -dir : 0))
})

function sortBy(key) {
  sort.value = sort.value.key === key ? { key, desc: !sort.value.desc } : { key, desc: key === 'time' || key === 'frp' }
}

const fixed = (v, d) => (v === null || v === undefined ? '–' : v.toFixed(d))
</script>

<template>
  <section class="obs">
    <p v-if="!supported" class="muted">{{ $t('obs.notSupported') }}</p>
    <template v-else>
      <!-- Filters (not tabs): each button shows only that satellite's rows. -->
      <div class="subtabs" role="group" :aria-label="$t('obs.satellites')">
        <button :aria-pressed="satellite === 'all'" :class="{ on: satellite === 'all' }" @click="satellite = 'all'">
          {{ $t('obs.all') }} <span>{{ num(rows.length) }}</span>
        </button>
        <button
          v-for="[s, n] in satellites" :key="s" :aria-pressed="satellite === s" :class="{ on: satellite === s }"
          @click="satellite = s"
        >
          {{ s }} <span>{{ num(n) }}</span>
        </button>
      </div>

      <div class="tools">
        <input v-model="filter" type="search" :placeholder="$t('obs.filter')" :aria-label="$t('obs.filterLabel')" />
        <div class="toggle" role="group" :aria-label="$t('obs.time')">
          <button :aria-pressed="!localTime" :class="{ on: !localTime }" @click="localTime = false">{{ $t('obs.utc') }}</button>
          <button :aria-pressed="localTime" :class="{ on: localTime }" @click="localTime = true">{{ $t('obs.local') }}</button>
        </div>
      </div>

      <p class="muted" aria-live="polite">
        {{ $t('obs.range', { start: date(store.queryParams.start), end: date(store.queryParams.end), dataset: store.currentDataset?.id ?? '' }) }}
        <span v-if="loading">{{ $t('obs.loading') }}</span>
      </p>
      <p v-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>
      <p v-if="truncated" class="muted">{{ $t('obs.truncated', { n: num(LIMIT) }) }}</p>

      <div class="table-wrap">
        <table>
          <caption class="sr-only">{{ $t('obs.caption') }}</caption>
          <thead>
            <tr>
              <th v-if="hexes.length > 1" scope="col">{{ $t('obs.cols.hexagon') }}</th>
              <th
                v-for="c in COLUMNS" :key="c.key" scope="col"
                :aria-sort="sort.key === c.key ? (sort.desc ? 'descending' : 'ascending') : 'none'"
              >
                <button @click="sortBy(c.key)">
                  {{ c.key === 'time' ? (localTime ? $t('obs.cols.timeLocal') : $t('obs.cols.timeUtc')) : $t(`obs.cols.${c.key}`) }}
                  <span v-if="sort.key === c.key" aria-hidden="true">{{ sort.desc ? '▼' : '▲' }}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(r, i) in visible" :key="i" :class="{ static: r.is_static_source }"
              :title="r.is_static_source ? $t('obs.staticRow') : undefined"
            >
              <td v-if="hexes.length > 1"><code>{{ r.h3 }}</code></td>
              <td class="nowrap">{{ timeOf(r) }}</td>
              <td>{{ r.satellite ?? '–' }}</td>
              <td>{{ r.product.toUpperCase() }}{{ r.is_preferred ? '' : '*' }}</td>
              <td>{{ fixed(r.latitude, 4) }}</td>
              <td>{{ fixed(r.longitude, 4) }}</td>
              <td>{{ fixed(r.frp, 1) }}</td>
              <td>{{ r.confidence_raw ?? '–' }}</td>
              <td>{{ r.daynight ?? '–' }}</td>
              <td>{{ fixed(r.brightness, 1) }}</td>
              <td>{{ fixed(r.brightness_aux, 1) }}</td>
              <td>{{ fixed(r.scan, 2) }}</td>
              <td>{{ fixed(r.track, 2) }}</td>
              <td>{{ r.version ?? '–' }}</td>
            </tr>
            <tr v-if="!visible.length && !loading">
              <td :colspan="COLUMNS.length + 1" class="empty">{{ $t('obs.empty') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted legend">
        <span class="swatch" aria-hidden="true"></span> {{ $t('obs.legendStatic') }} · {{ $t('obs.legendReplaced') }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.obs { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
.subtabs { display: flex; flex-wrap: wrap; gap: 4px; }
.subtabs button, .toggle button {
  border: 1px solid var(--border); background: var(--panel); color: var(--text); border-radius: 6px;
  padding: 4px 8px; font: inherit; font-size: 0.75rem; cursor: pointer;
}
.subtabs button span { color: var(--muted); }
.subtabs button.on, .toggle button.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.subtabs button.on span { color: var(--on-accent); }
.tools { display: flex; gap: 8px; align-items: center; }
.tools input {
  flex: 1; min-width: 0; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px;
  font: inherit; font-size: 0.75rem; color: var(--text); background: var(--bg);
}
.toggle { display: flex; gap: 2px; }
.table-wrap { overflow: auto; max-height: calc(100vh - 330px); border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; font-size: 0.75rem; font-variant-numeric: tabular-nums; width: max-content; min-width: 100%; }
th { position: sticky; top: 0; background: var(--surface-2); border-bottom: 1px solid var(--border); text-align: left; z-index: 1; }
th button {
  display: block; width: 100%; padding: 6px 8px; border: none; background: none; text-align: left;
  font: inherit; font-weight: 600; color: var(--text); white-space: nowrap; cursor: pointer;
}
td { padding: 4px 8px; border-bottom: 1px solid var(--surface-3); white-space: nowrap; }
tr.static td { color: var(--static); background: var(--surface-2); font-style: italic; }
.empty { text-align: center; color: var(--muted); padding: 16px; }
.muted { color: var(--muted); font-size: 0.75rem; margin: 0; }
.error { color: var(--danger); font-size: 0.75rem; margin: 0; }
.legend { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; background: var(--static); }
</style>
