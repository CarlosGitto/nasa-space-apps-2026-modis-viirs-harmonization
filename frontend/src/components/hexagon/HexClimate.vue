<script setup>
// Clima tab: the hexagon's weather next to its fire, day by day (NASA MERRA-2 + GPM IMERG).
// Covers the selected dates, widened to at least MIN_DAYS so a short range still shows the
// dry spell before a fire; the time slider's day is marked on every chart.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getHexagonPeriod, getHexagonWeather } from '../../api'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import { useFiltersStore } from '../../stores/filters'
import MiniChart from './MiniChart.vue'
import { dailyChart, dayRange, WEATHER_COLORS as C } from './charts'
import { cellsLabel, compass, daysBefore, merraMissing, weatherKpis } from './weather'
import './ficha.css'

const MIN_DAYS = 30

const props = defineProps({ h3: { type: String, required: true } })
const store = useFiltersStore()

const range = computed(() => {
  if (!store.end) return { start: null, end: null }
  const widest = daysBefore(store.end, MIN_DAYS - 1)
  return { start: store.start && store.start < widest ? store.start : widest, end: store.end }
})

const weather = ref(null)
const period = ref(null)
const error = ref(null)
const loading = ref(false)
let controller = null

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = null
  const { signal } = controller
  try {
    // Fire is optional context: a hexagon that never burned has no period (404), only weather.
    const [w, p] = await Promise.all([
      getHexagonWeather(props.h3, range.value, signal),
      getHexagonPeriod(props.h3, range.value, signal).catch((e) => (e.name === 'AbortError' ? Promise.reject(e) : null)),
    ])
    weather.value = w
    period.value = p
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e.message
    weather.value = null
  }
  loading.value = false
}
watch(() => [props.h3, range.value.start, range.value.end], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

// Every day of the range, so gaps show as gaps and all charts share the x axis.
const dates = computed(() => (range.value.start ? dayRange(range.value.start, range.value.end) : []))
const byDate = computed(() => Object.fromEntries((weather.value?.days ?? []).map((d) => [d.date, d])))
const days = computed(() => dates.value.map((d) => byDate.value[d] ?? { date: d }))
const series = (key) => days.value.map((d) => d[key] ?? null)
const hasWeather = computed(() => (weather.value?.days ?? []).length > 0)
const mark = computed(() => store.day)

const kpis = computed(() => weatherKpis(weather.value?.days ?? []))

// Fire next to drying power: detections per sensor (never added up) and the daily maximum VPD.
const fireChart = computed(() => {
  const sensors = (period.value?.sensors ?? []).filter((s) => s.fire_detections > 0)
  const bars = sensors.map((s) => {
    const n = Object.fromEntries(s.daily.map((d) => [d.date, d.detections]))
    return { name: SENSOR_LABELS[s.sensor], type: 'bar', data: dates.value.map((d) => n[d] ?? 0),
             color: SENSOR_COLORS[s.sensor], unit: 'detecciones', digits: 0 }
  })
  return dailyChart(dates.value, [
    ...bars,
    { name: 'VPD máx', type: 'line', axis: 1, data: series('vpd_max_kpa'), color: C.vpd, unit: 'kPa', digits: 2 },
  ], { axes: ['detecciones', 'kPa'], mark: mark.value })
})

const temperatureChart = computed(() => dailyChart(dates.value, [
  { name: 'Máx', type: 'line', data: series('t2m_max_c'), color: C.tmax, unit: '°C' },
  { name: 'Mín', type: 'line', data: series('t2m_min_c'), color: C.tmin, unit: '°C' },
  { name: 'Punto de rocío', type: 'line', data: series('dewpoint_mean_c'), color: C.dew, unit: '°C' },
], { axes: ['°C'], mark: mark.value }))

const humidityWindChart = computed(() => dailyChart(dates.value, [
  { name: 'Humedad mín', type: 'line', data: series('rh_min_pct'), color: C.rh, unit: '%', digits: 0 },
  { name: 'Viento máx', type: 'line', axis: 1, data: series('wind_speed_max_ms'), color: C.wind, unit: 'm/s' },
], {
  axes: ['%', 'm/s'], mark: mark.value,
  tooltipExtra: (i) => (days.value[i].wind_dir_mean_deg != null ? `Viento desde: <b>${compass(days.value[i].wind_dir_mean_deg)}</b>` : null),
}))

const rainChart = computed(() => dailyChart(dates.value, [
  { name: 'Lluvia del día', type: 'bar', data: series('precipitation_mm'), color: C.rain, unit: 'mm' },
  { name: 'Últimos 30 días', type: 'line', axis: 1, data: series('precipitation_30d_mm'), color: C.rain30, unit: 'mm' },
], {
  axes: ['mm', 'mm 30 d'], mark: mark.value,
  tooltipExtra: (i) => (days.value[i].days_since_rain != null ? `Días sin lluvia: <b>${days.value[i].days_since_rain}</b>` : null),
}))
</script>

<template>
  <article class="ficha">
    <header class="identity">
      <code>{{ h3 }}</code>
      <span class="muted">{{ range.start }} → {{ range.end }} · días UTC · NASA MERRA-2 + GPM IMERG</span>
    </header>
    <p v-if="loading && !weather" class="muted">Cargando…</p>
    <p v-else-if="error?.startsWith('no weather')" class="muted">
      Todavía no hay datos meteorológicos cargados (DAG <code>weather_ingest</code>).
    </p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <template v-else-if="weather">
      <p v-if="!hasWeather" class="muted">Sin datos meteorológicos para este lugar en estas fechas.</p>
      <template v-else>
        <dl class="kpis">
          <div v-for="k in kpis" :key="k.label">
            <dt>{{ k.label }}</dt>
            <dd>{{ k.value }}</dd>
            <dd v-if="k.note" class="note">{{ k.note }}</dd>
          </div>
        </dl>

        <section>
          <h4>Fuego y sequedad del aire</h4>
          <MiniChart :option="fireChart" height="170px" />
          <p v-if="!period" class="muted small">Este hexágono no tiene detecciones: solo se muestra el VPD.</p>
        </section>
        <section>
          <h4>Temperatura</h4>
          <MiniChart :option="temperatureChart" height="150px" />
        </section>
        <section>
          <h4>Humedad y viento</h4>
          <MiniChart :option="humidityWindChart" height="150px" />
        </section>
        <section>
          <h4>Lluvia</h4>
          <MiniChart :option="rainChart" height="150px" />
        </section>

        <p class="muted small">
          Celda más cercana: {{ cellsLabel(weather) }}. VPD = déficit de presión de vapor: cuánto seca
          el aire al combustible; sube con calor y baja humedad. Viento: medias horarias a 10 m, no ráfagas.
          <template v-if="merraMissing(days)"> Los últimos días todavía no tienen MERRA-2 (se publica con semanas de demora).</template>
        </p>
      </template>
    </template>
  </article>
</template>
