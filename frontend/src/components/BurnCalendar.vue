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
import { getHarmonizedMonthly, getMonthly } from '../api'
import { errorMessage, notBuilt, num } from '../format'
import { chartFont, months } from './hexagon/charts'
import { useFiltersStore } from '../stores/filters'
import { useLoadingBar } from '../stores/loading'
import { usePrefsStore } from '../stores/prefs'

echarts.use([AriaComponent, HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

const store = useFiltersStore()
const prefs = usePrefsStore()
const { t } = useI18n()
const el = ref(null)
const rows = ref([])
const loading = ref(false)
useLoadingBar('stats', loading)
const error = ref(null)
// Raw: the dataset chosen in the controls. Harmonized: one series across MODIS -> VIIRS (2000 onward), MODIS units.
const harmonized = ref(false)
let chart = null
let resizer = null
let controller = null

const years = computed(() => {
  const ys = rows.value.map((r) => r.year)
  return ys.length ? Array.from({ length: Math.max(...ys) - Math.min(...ys) + 1 }, (_, i) => Math.min(...ys) + i) : []
})
const height = computed(() => `${Math.max(160, years.value.length * chartFont(26) + chartFont(110))}px`)
// Harmonized detections are fractional (VIIRS / k): one decimal.
const count = (n) => (harmonized.value ? t('harm.detectionsModis', { n: num(n, 1) }) : t('common.detections', { n: num(n) }))

// Text alternative: the busiest month.
const label = computed(() => {
  if (!rows.value.length) return t('calendar.empty')
  const top = rows.value.reduce((a, b) => (b.detections > a.detections ? b : a))
  const values = { month: months()[top.month - 1], year: top.year, n: num(top.detections, harmonized.value ? 1 : 0) }
  return harmonized.value ? t('harm.calendarLabel', values) : t('calendar.label', values)
})
// The harmonized layer may not be built yet: say so instead of a generic 404.
const errorText = computed(() => (notBuilt(error.value) ? t('harm.notBuilt') : errorMessage(error.value)))

async function load() {
  if (!harmonized.value && (!store.dataset || !store.currentDataset?.available)) return
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  loading.value = true
  try {
    rows.value = harmonized.value
      ? await getHarmonizedMonthly({ zone: store.zone }, signal)
      : await getMonthly(store.dataset, { zone: store.zone }, signal)
    error.value = null
  } catch (e) {
    if (e.name !== 'AbortError') { error.value = e; rows.value = [] } // never the other mode's data under its toggle
  } finally {
    if (!signal.aborted) loading.value = false
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
        formatter: (p) => `${names[p.value[0]]} ${years.value[p.value[1]]}<br><b>${count(p.value[2])}</b>`,
      },
      grid: { left: chartFont(48), right: 8, top: 8, bottom: chartFont(72) },
      xAxis: { type: 'category', data: names, splitArea: { show: true }, axisLabel: { interval: 0, fontSize: chartFont(10) } },
      yAxis: { type: 'category', data: years.value.map(String), splitArea: { show: true }, axisLabel: { fontSize: chartFont(11) } },
      visualMap: {
        min: 0, max, orient: 'horizontal', left: 'center', bottom: 0,
        itemWidth: 12, itemHeight: 140, text: [num(max, harmonized.value ? 1 : 0), '0'], textGap: 8, textStyle: { fontSize: chartFont(11) },
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
// Data, language, text size or patterns changed: redraw.
watch(() => [rows.value, prefs.locale, prefs.textSize, prefs.easyReading], render)
</script>

<template>
  <section class="calendar">
    <h2>{{ $t('calendar.title', { where: (store.zone ? $t('calendar.zone') : $t('calendar.world')) }) }}</h2>
    <div class="mode" role="group" :aria-label="$t('harm.calendarValues')">
      <button type="button" :aria-pressed="!harmonized" :class="{ on: !harmonized }" @click="harmonized = false">{{ $t('harm.raw') }}</button>
      <button type="button" :aria-pressed="harmonized" :class="{ on: harmonized }" @click="harmonized = true">{{ $t('harm.harmonized') }}</button>
    </div>
    <p class="hint">
      {{ harmonized ? $t('harm.calendarHint') : $t('harm.calendarRawHint') }}
      {{ $t('calendar.hint') }}
    </p>
    <p v-if="error" class="error" role="alert">{{ errorText }}</p>
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
.mode { display: inline-flex; margin: 4px 0; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.mode button { font: inherit; font-size: 0.75rem; padding: 3px 10px; border: 0; background: var(--bg); color: var(--text); cursor: pointer; }
.mode button.on { background: var(--accent); color: var(--on-accent); }
</style>
