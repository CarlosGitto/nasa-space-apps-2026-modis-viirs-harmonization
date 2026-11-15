<script setup>
// Ficha · período: one hexagon over the selected dates (or the time slider's day).
// The backend adds up this hexagon's precomputed daily rows (fct_hexagon_daily).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHexagonPeriod } from '../../api'
import { date, errorMessage } from '../../format'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import { useFiltersStore } from '../../stores/filters'
import MiniChart from './MiniChart.vue'
import { chartLabel, dayLabel, dayRange, num, pct, sensorBars } from './charts'
import './ficha.css'

const props = defineProps({ h3: { type: String, required: true } })
const store = useFiltersStore()
const { t } = useI18n()

const period = ref(null)
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
let controller = null

const range = computed(() => ({ start: store.queryParams.start, end: store.queryParams.end }))

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = null
  try {
    period.value = await getHexagonPeriod(props.h3, range.value, controller.signal)
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e
    period.value = null
  }
  loading.value = false
}
watch(() => [props.h3, range.value.start, range.value.end], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

const mw = (v, digits = 0) => t('common.mw', { v: num(v, digits) })

const kpis = (s) => [
  { label: t('ficha.detections'), value: num(s.fire_detections) },
  { label: t('ficha.activeDays'), value: num(s.active_days) },
  { label: t('ficha.frpTotal'), value: mw(s.fire_frp_sum) },
  { label: t('ficha.frpMax'), value: mw(s.fire_frp_max, 1) },
  { label: t('ficha.highConf'), value: pct(s.high_conf_share) },
  { label: t('ficha.daytime'), value: pct(s.day_share) },
]

const satellites = (s) => Object.entries(s.satellites).map(([name, n]) => `${name} ${num(n)}`).join(' · ')

// Daily detections per sensor over every day of the range.
const dailySeries = computed(() => {
  const sensors = period.value?.sensors ?? []
  if (!sensors.some((s) => s.fire_detections > 0) || !range.value.start || !range.value.end) return null
  const days = dayRange(range.value.start, range.value.end)
  const bySensor = {}
  for (const s of sensors) {
    bySensor[s.sensor] = new Array(days.length).fill(0)
    for (const d of s.daily) bySensor[s.sensor][days.indexOf(d.date)] = d.detections
  }
  return { categories: days.map(dayLabel), bySensor }
})
</script>

<template>
  <article class="ficha">
    <header class="identity">
      <code>{{ h3 }}</code>
      <span class="muted">{{ $t('ficha.periodRange', { start: date(range.start), end: date(range.end) }) }}</span>
    </header>
    <p v-if="loading && !period" class="muted">{{ $t('common.loading') }}</p>
    <p v-else-if="error?.status === 404" class="muted">{{ $t('ficha.noHistory') }}</p>
    <p v-else-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>
    <template v-else-if="period">
      <p v-if="!period.sensors.length" class="muted">{{ $t('ficha.noPeriod') }}</p>
      <section v-for="s in period.sensors" :key="s.sensor" class="sensor">
        <h3><span class="dot" :style="{ background: SENSOR_COLORS[s.sensor] }" aria-hidden="true"></span>{{ SENSOR_LABELS[s.sensor] }}</h3>
        <dl class="kpis">
          <div v-for="k in kpis(s)" :key="k.label">
            <dt>{{ k.label }}</dt>
            <dd>{{ k.value }}</dd>
          </div>
        </dl>
        <p v-if="s.fire_detections" class="muted small">{{ $t('ficha.bySatellite', { list: satellites(s) }) }}</p>
        <p v-if="s.static_detections" class="muted small">{{ $t('ficha.staticPeriod', { n: num(s.static_detections) }) }}</p>
      </section>
      <section v-if="dailySeries">
        <h3>{{ $t('ficha.daily') }}</h3>
        <MiniChart
          :option="sensorBars(dailySeries.categories, dailySeries.bySensor)"
          :label="chartLabel($t('ficha.daily'), dailySeries.categories, dailySeries.bySensor)"
        />
      </section>
    </template>
  </article>
</template>
