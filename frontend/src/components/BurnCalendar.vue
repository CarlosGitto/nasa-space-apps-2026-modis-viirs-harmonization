<script setup>
// Burn calendar: detections per month (x) and year (y) for the current
// dataset and zone (whole world when no zone). Click a cell to view that month.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { HeatmapChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getMonthly } from '../api'
import { useFiltersStore } from '../stores/filters'

echarts.use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

const store = useFiltersStore()
const el = ref(null)
const years = ref([])
const error = ref(null)
let chart = null
let resizer = null
let controller = null

const title = computed(() => `Calendario de quema · ${store.zone ? 'Zona dibujada' : 'Mundo'}`)
const height = computed(() => `${Math.max(160, years.value.length * 26 + 110)}px`)
const fmt = (n) => (n ?? 0).toLocaleString('es-AR')

async function load() {
  if (!store.dataset || !store.currentDataset?.available) return
  controller?.abort()
  controller = new AbortController()
  let rows
  try {
    rows = await getMonthly(store.dataset, { zone: store.zone }, controller.signal)
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
        formatter: (p) => `${MONTHS[p.value[0]]} ${years.value[p.value[1]]}<br><b>${fmt(p.value[2])}</b> detecciones`,
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
  chart.on('click', (p) => store.setMonth(years.value[p.value[1]], p.value[0] + 1))
  resizer = new ResizeObserver(() => chart.resize())
  resizer.observe(el.value)
  load()
})

onBeforeUnmount(() => {
  controller?.abort()
  resizer?.disconnect()
  chart?.dispose()
})

watch(() => [store.dataset, store.zone], load)
</script>

<template>
  <section class="calendar">
    <h2>{{ title }}</h2>
    <p class="hint">Click en un mes para verlo en el mapa.</p>
    <p v-if="error" class="error">{{ error }}</p>
    <div ref="el" class="chart" :style="{ height }"></div>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 14px; }
.hint { margin: 2px 0 6px; font-size: 12px; color: var(--muted); }
.error { color: var(--danger); font-size: 12px; }
.chart { width: 100%; }
</style>
