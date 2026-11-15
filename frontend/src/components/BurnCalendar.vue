<script setup>
// Burn calendar: detections per month (x) and year (y) for the current
// dataset and zone (whole world when no zone). Click a cell to view that month.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { HeatmapChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getHarmonizedMonthly, getMonthly } from '../api'
import { useFiltersStore } from '../stores/filters'

echarts.use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

const store = useFiltersStore()
const el = ref(null)
const years = ref([])
const error = ref(null)
// Raw: the dataset chosen in the controls. Harmonized: one series across MODIS -> VIIRS (2000 onward), MODIS units.
const harmonized = ref(false)
let chart = null
let resizer = null
let controller = null

const title = computed(() => `Calendario de quema · ${store.zone ? 'Zona dibujada' : 'Mundo'}`)
const unit = computed(() => (harmonized.value ? 'detecciones (unidades MODIS)' : 'detecciones'))
const height = computed(() => `${Math.max(160, years.value.length * 26 + 110)}px`)
const fmt = (n) => (n ?? 0).toLocaleString('es-AR', { maximumFractionDigits: harmonized.value ? 1 : 0 })

async function load() {
  if (!harmonized.value && (!store.dataset || !store.currentDataset?.available)) return
  controller?.abort()
  controller = new AbortController()
  let rows
  try {
    rows = harmonized.value
      ? await getHarmonizedMonthly({ zone: store.zone }, controller.signal)
      : await getMonthly(store.dataset, { zone: store.zone }, controller.signal)
    error.value = null
  } catch (e) {
    if (e.name !== 'AbortError') error.value = e.message
    return
  }
  const ys = rows.map((r) => r.year)
  years.value = ys.length ? Array.from({ length: Math.max(...ys) - Math.min(...ys) + 1 }, (_, i) => Math.min(...ys) + i) : []
  const data = rows.map((r) => [r.month - 1, years.value.indexOf(r.year), r.detections])
  const max = Math.max(1, ...rows.map((r) => r.detections))

  chart.setOption(
    {
      tooltip: {
        formatter: (p) => `${MONTHS[p.value[0]]} ${years.value[p.value[1]]}<br><b>${fmt(p.value[2])}</b> ${unit.value}`,
      },
      grid: { left: 48, right: 8, top: 8, bottom: 72 },
      xAxis: { type: 'category', data: MONTHS, splitArea: { show: true }, axisLabel: { interval: 0, fontSize: 10 } },
      yAxis: { type: 'category', data: years.value.map(String), splitArea: { show: true } },
      visualMap: {
        min: 0, max, orient: 'horizontal', left: 'center', bottom: 0,
        itemWidth: 12, itemHeight: 140, text: [fmt(max), '0'], textGap: 8, textStyle: { fontSize: 11 },
        inRange: { color: ['#fff7ed', '#fdba74', '#f97316', '#dc2626', '#7f1d1d'] },
      },
      series: [{
        type: 'heatmap', data,
        emphasis: { itemStyle: { borderColor: '#111', borderWidth: 1 } },
      }],
    },
    true,
  )
  // Height depends on the number of years.
  requestAnimationFrame(() => chart.resize())
}

onMounted(() => {
  chart = echarts.init(el.value)
  chart.on('click', (p) => {
    const [year, month] = [years.value[p.value[1]], p.value[0] + 1]
    // The harmonized calendar reaches back to 2000; the map only has the months of the chosen dataset.
    const first = store.currentDataset?.start_date
    if (first && new Date(Date.UTC(year, month, 0)) < new Date(`${first}T00:00:00Z`)) return
    store.setMonth(year, month)
  })
  resizer = new ResizeObserver(() => chart.resize())
  resizer.observe(el.value)
  load()
})

onBeforeUnmount(() => {
  controller?.abort()
  resizer?.disconnect()
  chart?.dispose()
})

watch(() => [store.dataset, store.zone, harmonized.value], load)
</script>

<template>
  <section class="calendar">
    <h2>{{ title }}</h2>
    <div class="mode" role="group" aria-label="Valores del calendario">
      <button :class="{ on: !harmonized }" @click="harmonized = false">Raw</button>
      <button :class="{ on: harmonized }" @click="harmonized = true">Armonizado</button>
    </div>
    <p class="hint">
      <template v-if="harmonized">
        Una sola serie 2000 → hoy en unidades MODIS: MODIS hasta ene 2012, VIIRS S-NPP ÷ k desde feb 2012.
      </template>
      <template v-else>Datos del dataset elegido, sin ajustar.</template>
      Click en un mes para verlo en el mapa.
    </p>
    <p v-if="error" class="error">{{ error }}</p>
    <div ref="el" class="chart" :style="{ height }"></div>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 14px; }
.hint { margin: 2px 0 6px; font-size: 12px; color: var(--muted); }
.error { color: var(--danger); font-size: 12px; }
.chart { width: 100%; }
.mode { display: inline-flex; margin: 4px 0; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.mode button { font: inherit; font-size: 12px; padding: 3px 10px; border: 0; background: var(--bg); color: var(--text); cursor: pointer; }
.mode button.on { background: var(--accent, #2563eb); color: #fff; }
</style>
