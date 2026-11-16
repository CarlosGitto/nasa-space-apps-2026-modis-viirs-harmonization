<script setup>
// "Detalle" for many hexagons (a drawn area): one row per hexagon with detections,
// busiest first; click a row to open its ficha. It follows the dates and the time
// slider, so a hexagon of the area that burns on the day shown comes up here.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getSelectionSummary } from '../../api'
import { date, errorMessage } from '../../format'
import { useFiltersStore } from '../../stores/filters'
import { useLoadingBar } from '../../stores/loading'
import HexClimate from './HexClimate.vue'
import HexHistory from './HexHistory.vue'
import HexPeriod from './HexPeriod.vue'
import { num } from './charts'

const PAGE = 50

const props = defineProps({
  hexes: { type: Array, required: true },
  kind: { type: String, required: true }, // 'history' | 'period' | 'weather' (ranked like 'period')
})
const store = useFiltersStore()

const summary = ref([])
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
useLoadingBar('panel', loading)
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
    error.value = e
    summary.value = []
  }
  loading.value = false
}
watch(() => [props.hexes, store.queryParams.start, store.queryParams.end], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

const isPeriod = computed(() => props.kind !== 'history')
const rows = computed(() => {
  const list = isPeriod.value
    ? summary.value.filter((r) => r.modis_period + r.viirs_period + r.static_period > 0)
    : summary.value.filter((r) => r.modis_total + r.viirs_total > 0)
  const total = (r) => (isPeriod.value ? r.modis_period + r.viirs_period : r.modis_total + r.viirs_total)
  return [...list].sort((a, b) => total(b) - total(a))
})
const empty = computed(() => props.hexes.length - rows.value.length)

const summaryValues = computed(() => ({
  with: num(rows.value.length), total: num(props.hexes.length), empty: num(empty.value),
  start: date(store.queryParams.start), end: date(store.queryParams.end),
}))

const toggle = (h3) => { open.value = open.value === h3 ? null : h3 }
</script>

<template>
  <section class="ranking">
    <p class="muted" aria-live="polite">
      {{ isPeriod ? $t('ranking.summaryPeriod', summaryValues) : $t('ranking.summaryHistory', summaryValues) }}
      <span v-if="loading">{{ $t('obs.loading') }}</span>
    </p>
    <p v-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>

    <table v-if="rows.length">
      <caption class="sr-only">{{ $t('ranking.caption') }}</caption>
      <thead>
        <tr>
          <th scope="col">{{ $t('ranking.hexagon') }}</th>
          <th scope="col" class="n">MODIS</th>
          <th scope="col" class="n">VIIRS</th>
          <th scope="col">{{ (isPeriod ? $t('ranking.staticSource') : $t('ranking.lastDetection')) }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="r in rows.slice(0, shown)" :key="r.h3">
          <!-- The whole row is clickable; the button in its first cell is the keyboard and screen-reader way in. -->
          <tr class="row" :class="{ open: open === r.h3 }" @click="toggle(r.h3)">
            <td>
              <button
                class="expand" :aria-expanded="open === r.h3" :aria-controls="`ficha-${r.h3}`"
                :aria-label="$t('ranking.open', { id: r.h3 })" @click.stop="toggle(r.h3)"
              >
                <span class="caret" aria-hidden="true">{{ open === r.h3 ? '▾' : '▸' }}</span>
                <code>{{ r.h3 }}</code>
              </button>
            </td>
            <td class="n">{{ num(isPeriod ? r.modis_period : r.modis_total) }}</td>
            <td class="n">{{ num(isPeriod ? r.viirs_period : r.viirs_total) }}</td>
            <td>{{ isPeriod ? (r.static_period ? num(r.static_period) : '–') : date(r.last_date) }}</td>
          </tr>
          <tr v-if="open === r.h3" :id="`ficha-${r.h3}`">
            <td colspan="4" class="ficha-cell">
              <HexHistory v-if="kind === 'history'" :h3="r.h3" />
              <HexPeriod v-else-if="kind === 'period'" :h3="r.h3" />
              <HexClimate v-else :h3="r.h3" />
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <button v-if="rows.length > shown" class="more" @click="shown += PAGE">
      {{ $t('ranking.showMore', { n: num(Math.min(PAGE, rows.length - shown)) }) }}
    </button>
    <p class="muted small">{{ kind === 'weather' ? $t('ranking.footerWeather') : $t('ranking.footer') }}</p>
  </section>
</template>

<style scoped>
.ranking { display: flex; flex-direction: column; gap: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 0.75rem; font-variant-numeric: tabular-nums; }
th { text-align: left; font-weight: 600; padding: 6px; border-bottom: 1px solid var(--border); background: var(--surface-2); }
td { padding: 5px 6px; border-bottom: 1px solid var(--surface-3); }
.n { text-align: right; }
.row { cursor: pointer; }
.row:hover, .row.open { background: var(--highlight); }
.expand {
  display: inline-flex; gap: 6px; align-items: center; border: none; background: none; padding: 0;
  font: inherit; color: var(--text); cursor: pointer;
}
.caret { color: var(--muted); width: 10px; }
.ficha-cell { padding: 10px; background: var(--surface-1); }
.more {
  align-self: flex-start; border: 1px solid var(--border); background: var(--panel); border-radius: 6px;
  padding: 4px 10px; font: inherit; font-size: 0.75rem; color: var(--text); cursor: pointer;
}
.muted { color: var(--muted); font-size: 0.75rem; margin: 0; }
.small { font-size: 0.6875rem; }
.error { color: var(--danger); font-size: 0.75rem; margin: 0; }
</style>
