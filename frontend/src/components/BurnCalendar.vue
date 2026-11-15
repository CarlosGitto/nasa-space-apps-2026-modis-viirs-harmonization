<script setup>
// Burn calendar: detections per month (x) and year (y) for the current
// dataset and zone (whole world when no zone). Click a cell to view that month.
// The heatmap has a text alternative (busiest month) for screen readers; the same
// dates can be picked with the date inputs.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import * as echarts from 'echarts/core'
import { HeatmapChart } from 'echarts/charts'
import { AriaComponent, GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getMonthly } from '../api'
import { errorMessage, num } from '../format'
import { chartFont, months } from './hexagon/charts'
import { useFiltersStore } from '../stores/filters'
import { usePrefsStore } from '../stores/prefs'

echarts.use([AriaComponent, HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

const store = useFiltersStore()
const prefs = usePrefsStore()
const { t } = useI18n()
const el = ref(null)
const rows = ref([])
const error = ref(null)
let chart = null
let resizer = null
let controller = null

const years = computed(() => {
  const ys = rows.value.map((r) => r.year)
  return ys.length ? Array.from({ length: Math.max(...ys) - Math.min(...ys) + 1 }, (_, i) => Math.min(...ys) + i) : []
})
const height = computed(() => `${Math.max(160, years.value.length * chartFont(26) + chartFont(110))}px`)

// Text alternative: the busiest month.
const label = computed(() => {
  if (!rows.value.length) return t('calendar.empty')
  const top = rows.value.reduce((a, b) => (b.detections > a.detections ? b : a))
  return t('calendar.label', { month: months()[top.month - 1], year: top.year, n: num(top.detections) })
})

async function load() {
  if (!store.dataset || !store.currentDataset?.available) return
  controller?.abort()
  controller = new AbortController()
  try {
    rows.value = await getMonthly(store.dataset, { zone: store.zone }, controller.signal)
    error.value = null
  } catch (e) {
    if (e.name !== 'AbortError') error.value = e
  }
}

function render() {
  if (!chart) return
  const names = months()
  const data = rows.value.map((r) => [r.month - 1, years.value.indexOf(r.year), r.detections])
  const max = Math.max(1, ...rows.value.map((r) => r.detections))
  chart.setOption(
    {
      aria: { enabled: prefs.easyReading, label: { enabled: false }, decal: { show: prefs.easyReading } },
      tooltip: {
        textStyle: { fontSize: chartFont(12) },
        formatter: (p) => `${names[p.value[0]]} ${years.value[p.value[1]]}<br><b>${t('common.detections', { n: num(p.value[2]) })}</b>`,
      },
      grid: { left: chartFont(48), right: 8, top: 8, bottom: chartFont(72) },
      xAxis: { type: 'category', data: names, splitArea: { show: true }, axisLabel: { interval: 0, fontSize: chartFont(10) } },
      yAxis: { type: 'category', data: years.value.map(String), splitArea: { show: true }, axisLabel: { fontSize: chartFont(11) } },
      visualMap: {
        min: 0, max, orient: 'horizontal', left: 'center', bottom: 0,
        itemWidth: 12, itemHeight: 140, text: [num(max), '0'], textGap: 8, textStyle: { fontSize: chartFont(11) },
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
  requestAnimationFrame(() => chart?.resize())
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
// Data, language, text size or patterns changed: redraw.
watch(() => [rows.value, prefs.locale, prefs.textSize, prefs.easyReading], render)
</script>

<template>
  <section class="calendar">
    <h2>{{ $t('calendar.title', { where: (store.zone ? $t('calendar.zone') : $t('calendar.world')) }) }}</h2>
    <p class="hint">{{ $t('calendar.hint') }}</p>
    <p v-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>
    <div role="img" :aria-label="label">
      <div ref="el" class="chart" :style="{ height }"></div>
    </div>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 0.875rem; }
.hint { margin: 2px 0 6px; font-size: 0.75rem; color: var(--muted); }
.error { color: var(--danger); font-size: 0.75rem; }
.chart { width: 100%; }
</style>
