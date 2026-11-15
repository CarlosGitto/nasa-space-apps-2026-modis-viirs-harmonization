<script setup>
// Ficha · período: one hexagon over the selected dates (or the time slider's day).
// The backend adds up this hexagon's precomputed daily rows (fct_hexagon_daily).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getHexagonPeriod } from '../../api'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import { useFiltersStore } from '../../stores/filters'
import MiniChart from './MiniChart.vue'
import { dayRange, num, pct, sensorBars } from './charts'
import './ficha.css'

const props = defineProps({ h3: { type: String, required: true } })
const store = useFiltersStore()

const period = ref(null)
const error = ref(null)
const loading = ref(false)
let controller = null

const h = computed(() => store.hexHarmonized) // MODIS units: counts are fractional, no confidence or satellite there
const n = (v) => num(v, h.value ? 1 : 0)
const range = computed(() => ({ start: store.queryParams.start, end: store.queryParams.end }))

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = null
  try {
    period.value = await getHexagonPeriod(props.h3, { ...range.value, harmonized: h.value || null }, controller.signal)
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e.message
    period.value = null
  }
  loading.value = false
}
watch(() => [props.h3, range.value.start, range.value.end, h.value], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

const kpis = (s) => [
  { label: 'Detecciones', value: n(s.fire_detections) },
  { label: 'Días con fuego', value: num(s.active_days) },
  { label: 'FRP total', value: `${num(s.fire_frp_sum)} MW` },
  { label: 'FRP máx', value: `${num(s.fire_frp_max, 1)} MW` },
  { label: 'Alta confianza', value: pct(s.high_conf_share) },
  { label: 'Diurnas', value: pct(s.day_share) },
].filter((k) => !(h.value && k.label === 'Alta confianza'))

const satellites = (s) => Object.entries(s.satellites).map(([name, n]) => `${name} ${num(n)}`).join(' · ')

// Daily detections per sensor over every day of the range.
const dailyChart = computed(() => {
  const sensors = period.value?.sensors ?? []
  if (!sensors.some((s) => s.fire_detections > 0) || !range.value.start || !range.value.end) return null
  const days = dayRange(range.value.start, range.value.end)
  const bySensor = {}
  for (const s of sensors) {
    bySensor[s.sensor] = new Array(days.length).fill(0)
    for (const d of s.daily) bySensor[s.sensor][days.indexOf(d.date)] = d.detections
  }
  return sensorBars(days.map((d) => d.slice(5)), bySensor, { decimals: h.value ? 1 : 0 })
})
</script>

<template>
  <article class="ficha">
    <header class="identity">
      <code>{{ h3 }}</code>
      <span class="muted">{{ range.start }} → {{ range.end }} · serie continua (SP + NRT){{ h ? ' · unidades MODIS (armonizado)' : '' }}</span>
    </header>
    <p v-if="loading && !period" class="muted">Cargando…</p>
    <p v-else-if="error?.startsWith('no detections')" class="muted">Sin detecciones en toda la historia de este hexágono.</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <template v-else-if="period">
      <p v-if="!period.sensors.length" class="muted">Sin detecciones en estas fechas.</p>
      <section v-for="s in period.sensors" :key="s.sensor" class="sensor">
        <h4><span class="dot" :style="{ background: SENSOR_COLORS[s.sensor] }"></span>{{ SENSOR_LABELS[s.sensor] }}</h4>
        <dl class="kpis">
          <div v-for="k in kpis(s)" :key="k.label">
            <dt>{{ k.label }}</dt>
            <dd>{{ k.value }}</dd>
          </div>
        </dl>
        <p v-if="s.fire_detections && !h" class="muted small">Por satélite: {{ satellites(s) }}</p>
        <p v-if="s.static_detections && !h" class="muted small">
          + {{ num(s.static_detections) }} detecciones de fuente fija (no son incendios)
        </p>
      </section>
      <section v-if="dailyChart">
        <h4>Detecciones por día</h4>
        <MiniChart :option="dailyChart" />
      </section>
    </template>
  </article>
</template>
