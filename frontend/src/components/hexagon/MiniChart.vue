<script setup>
// Small ECharts chart for the hexagon panel: takes a full option object.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '160px' },
})

const el = ref(null)
let chart = null
let resizer = null

onMounted(() => {
  chart = echarts.init(el.value)
  chart.setOption(props.option, true)
  resizer = new ResizeObserver(() => chart.resize())
  resizer.observe(el.value)
})

onBeforeUnmount(() => {
  resizer?.disconnect()
  chart?.dispose()
})

watch(() => props.option, (option) => chart?.setOption(option, true))
</script>

<template>
  <div ref="el" :style="{ width: '100%', height }"></div>
</template>
