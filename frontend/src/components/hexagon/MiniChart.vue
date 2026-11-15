<script setup>
// Small ECharts chart for the hexagon panel: takes a full option object and a text
// alternative (`label`) for screen readers. With "easy reading" on, bars also get
// patterns, so sensors aren't told apart by color alone.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { usePrefsStore } from '../../stores/prefs'

echarts.use([AriaComponent, BarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  option: { type: Object, required: true },
  label: { type: String, required: true },
  height: { type: String, default: '160px' },
})

const prefs = usePrefsStore()
const el = ref(null)
let chart = null
let resizer = null

// ECharts' own aria text is English-only: ours goes on the wrapper (role="img").
const render = () => chart?.setOption({
  ...props.option,
  aria: { enabled: prefs.easyReading, label: { enabled: false }, decal: { show: prefs.easyReading } },
}, true)

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  resizer = new ResizeObserver(() => chart.resize())
  resizer.observe(el.value)
})

onBeforeUnmount(() => {
  resizer?.disconnect()
  chart?.dispose()
})

watch(() => [props.option, prefs.easyReading], render)
</script>

<template>
  <div role="img" :aria-label="label">
    <div ref="el" :style="{ width: '100%', height }"></div>
  </div>
</template>
