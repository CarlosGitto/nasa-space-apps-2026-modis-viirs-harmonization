<script setup>
// "Detalle" for many hexagons (a drawn area): one row per hexagon with detections,
// busiest first; click a row to open its ficha. It follows the dates and the time
// slider, so a hexagon of the area that burns on the day shown comes up here.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getSelectionSummary } from '../../api'
import { useFiltersStore } from '../../stores/filters'
import HexHistory from './HexHistory.vue'
import HexPeriod from './HexPeriod.vue'
import { num } from './charts'

const PAGE = 50

const props = defineProps({
  hexes: { type: Array, required: true },
  kind: { type: String, required: true }, // 'history' | 'period'
})
const store = useFiltersStore()

const summary = ref([])
const error = ref(null)
const loading = ref(false)
const open = ref(null) // h3 whose ficha is expanded
const shown = ref(PAGE)
let controller = null

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = null
  try {
    const { start, end } = store.queryParams
    summary.value = await getSelectionSummary({ h3: props.hexes, start, end }, controller.signal)
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e.message
    summary.value = []
  }
  loading.value = false
}
watch(() => [props.hexes, store.queryParams.start, store.queryParams.end], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

const isPeriod = computed(() => props.kind === 'period')
const rows = computed(() => {
  const list = isPeriod.value
    ? summary.value.filter((r) => r.modis_period + r.viirs_period + r.static_period > 0)
    : summary.value.filter((r) => r.modis_total + r.viirs_total > 0)
  const total = (r) => (isPeriod.value ? r.modis_period + r.viirs_period : r.modis_total + r.viirs_total)
  return [...list].sort((a, b) => total(b) - total(a))
})
const empty = computed(() => props.hexes.length - rows.value.length)

const toggle = (h3) => { open.value = open.value === h3 ? null : h3 }
</script>

<template>
  <section class="ranking">
    <p class="muted">
      <b>{{ num(rows.length) }}</b> de {{ num(hexes.length) }} hexágonos con detecciones
      {{ isPeriod ? `entre ${store.queryParams.start} y ${store.queryParams.end}` : 'en toda la historia' }}
      · {{ num(empty) }} sin detecciones
      <span v-if="loading"> · cargando…</span>
    </p>
    <p v-if="error" class="error">{{ error }}</p>

    <table v-if="rows.length">
      <thead>
        <tr>
          <th>Hexágono</th>
          <th class="n">MODIS</th>
          <th class="n">VIIRS</th>
          <th>{{ isPeriod ? 'Fuente fija' : 'Última detección' }}</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="r in rows.slice(0, shown)" :key="r.h3">
          <tr class="row" :class="{ open: open === r.h3 }" @click="toggle(r.h3)">
            <td><code>{{ r.h3 }}</code></td>
            <td class="n">{{ num(isPeriod ? r.modis_period : r.modis_total) }}</td>
            <td class="n">{{ num(isPeriod ? r.viirs_period : r.viirs_total) }}</td>
            <td>{{ isPeriod ? (r.static_period ? num(r.static_period) : '–') : (r.last_date ?? '–') }}</td>
            <td class="caret">{{ open === r.h3 ? '▾' : '▸' }}</td>
          </tr>
          <tr v-if="open === r.h3">
            <td colspan="5" class="ficha-cell">
              <HexHistory v-if="!isPeriod" :h3="r.h3" />
              <HexPeriod v-else :h3="r.h3" />
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <button v-if="rows.length > shown" class="more" @click="shown += PAGE">
      Mostrar {{ num(Math.min(PAGE, rows.length - shown)) }} más
    </button>
    <p class="muted small">Detecciones de incendio (serie continua SP + NRT). Click en una fila para ver su ficha.</p>
  </section>
</template>

<style scoped>
.ranking { display: flex; flex-direction: column; gap: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; font-variant-numeric: tabular-nums; }
th { text-align: left; font-weight: 600; padding: 6px; border-bottom: 1px solid var(--border); background: #f9fafb; }
td { padding: 5px 6px; border-bottom: 1px solid #f3f4f6; }
.n { text-align: right; }
.row { cursor: pointer; }
.row:hover, .row.open { background: #eff6ff; }
.caret { color: var(--muted); width: 16px; }
.ficha-cell { padding: 10px; background: #fcfcfd; }
.more {
  align-self: flex-start; border: 1px solid var(--border); background: var(--panel); border-radius: 6px;
  padding: 4px 10px; font: inherit; font-size: 12px; cursor: pointer;
}
.muted { color: var(--muted); font-size: 12px; margin: 0; }
.small { font-size: 11px; }
.error { color: var(--danger); font-size: 12px; margin: 0; }
</style>
