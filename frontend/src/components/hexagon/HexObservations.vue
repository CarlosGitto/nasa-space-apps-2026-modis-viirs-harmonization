<script setup>
// Observaciones: every detection of the selected hexagons, like FIRMS' table.
// Respects the chosen dataset and dates (or the time slider's day). One request for
// the whole selection (a drawn area can hold thousands of hexagons).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getSelectionDetections } from '../../api'
import { STATIC_COLOR } from '../../mapShared'
import { useFiltersStore } from '../../stores/filters'
import { num } from './charts'

const LIMIT = 5000

const props = defineProps({ hexes: { type: Array, required: true } })
const store = useFiltersStore()

const rows = ref([])
const truncated = ref(false)
const error = ref(null)
const loading = ref(false)
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
    error.value = e.message
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

const COLUMNS = [
  { key: 'time', label: 'Fecha y hora', value: (r) => r.acq_datetime_utc },
  { key: 'satellite', label: 'Satélite', value: (r) => r.satellite ?? '' },
  { key: 'product', label: 'Prod.', value: (r) => r.product },
  { key: 'latitude', label: 'Lat', value: (r) => r.latitude },
  { key: 'longitude', label: 'Lon', value: (r) => r.longitude },
  { key: 'frp', label: 'FRP (MW)', value: (r) => r.frp ?? -1 },
  { key: 'confidence', label: 'Conf.', value: (r) => r.confidence_raw ?? '' },
  { key: 'daynight', label: 'D/N', value: (r) => r.daynight ?? '' },
  { key: 'brightness', label: 'Brillo (K)', value: (r) => r.brightness ?? -1 },
  { key: 'brightness_aux', label: 'Brillo aux (K)', value: (r) => r.brightness_aux ?? -1 },
  { key: 'scan', label: 'Scan', value: (r) => r.scan ?? -1 },
  { key: 'track', label: 'Track', value: (r) => r.track ?? -1 },
  { key: 'version', label: 'Versión', value: (r) => r.version ?? '' },
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
    <p v-if="!supported" class="muted">Este dataset no tiene detalle por hexágono: elegí MODIS o VIIRS.</p>
    <template v-else>
      <div class="subtabs" role="tablist">
        <button :class="{ on: satellite === 'all' }" role="tab" @click="satellite = 'all'">
          Todos <span>{{ num(rows.length) }}</span>
        </button>
        <button v-for="[s, n] in satellites" :key="s" :class="{ on: satellite === s }" role="tab" @click="satellite = s">
          {{ s }} <span>{{ num(n) }}</span>
        </button>
      </div>

      <div class="tools">
        <input v-model="filter" type="search" placeholder="Filtrar…" aria-label="Filtrar observaciones" />
        <div class="toggle" role="group" aria-label="Hora">
          <button :class="{ on: !localTime }" @click="localTime = false">UTC</button>
          <button :class="{ on: localTime }" @click="localTime = true">Hora solar local</button>
        </div>
      </div>

      <p class="muted">
        {{ store.queryParams.start }} → {{ store.queryParams.end }} · {{ store.currentDataset?.id }}
        <span v-if="loading"> · cargando…</span>
      </p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="truncated" class="muted">
        Se muestran las {{ num(LIMIT) }} más recientes: acortá las fechas para ver todas.
      </p>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th v-if="hexes.length > 1">Hexágono</th>
              <th v-for="c in COLUMNS" :key="c.key" :aria-sort="sort.key === c.key ? (sort.desc ? 'descending' : 'ascending') : 'none'">
                <button @click="sortBy(c.key)">
                  {{ c.key === 'time' ? (localTime ? 'Fecha y hora (solar)' : 'Fecha y hora (UTC)') : c.label }}
                  <span v-if="sort.key === c.key">{{ sort.desc ? '▼' : '▲' }}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in visible" :key="i" :class="{ static: r.is_static_source }"
                :title="r.is_static_source ? 'Fuente fija de calor: no es un incendio' : ''">
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
              <td :colspan="COLUMNS.length + 1" class="empty">Sin observaciones.</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted legend">
        <span class="swatch" :style="{ background: STATIC_COLOR }"></span> gris: fuente fija (no es incendio) ·
        * NRT reemplazado por SP (no entra en la serie continua)
      </p>
    </template>
  </section>
</template>

<style scoped>
.obs { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
.subtabs { display: flex; flex-wrap: wrap; gap: 4px; }
.subtabs button, .toggle button {
  border: 1px solid var(--border); background: var(--panel); color: var(--text); border-radius: 6px;
  padding: 4px 8px; font: inherit; font-size: 12px; cursor: pointer;
}
.subtabs button span { color: var(--muted); }
.subtabs button.on, .toggle button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.subtabs button.on span { color: #dbeafe; }
.tools { display: flex; gap: 8px; align-items: center; }
.tools input { flex: 1; min-width: 0; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font: inherit; font-size: 12px; }
.toggle { display: flex; gap: 2px; }
.table-wrap { overflow: auto; max-height: calc(100vh - 330px); border: 1px solid var(--border); border-radius: 6px; }
table { border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; width: max-content; min-width: 100%; }
th { position: sticky; top: 0; background: #f9fafb; border-bottom: 1px solid var(--border); text-align: left; z-index: 1; }
th button { all: unset; cursor: pointer; display: block; padding: 6px 8px; font-weight: 600; white-space: nowrap; }
td { padding: 4px 8px; border-bottom: 1px solid #f3f4f6; white-space: nowrap; }
tr.static td { color: v-bind(STATIC_COLOR); background: #f9fafb; font-style: italic; }
.empty { text-align: center; color: var(--muted); padding: 16px; }
.muted { color: var(--muted); font-size: 12px; margin: 0; }
.error { color: var(--danger); font-size: 12px; margin: 0; }
.legend { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
</style>
