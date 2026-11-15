<script setup>
// Clima tab: the hexagon's weather next to its fire, day by day (NASA POWER, on demand).
// Covers the selected dates, widened to at least MIN_DAYS so a short range still shows the
// dry spell before a fire; the time slider's day is marked on every chart.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHexagonPeriod, getHexagonWeather } from '../../api'
import { date, errorMessage } from '../../format'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import { useFiltersStore } from '../../stores/filters'
import { useLoadingBar } from '../../stores/loading'
import MiniChart from './MiniChart.vue'
import WeatherCoverage from './WeatherCoverage.vue'
import { dailyChart, dailyChartLabel, dayRange, num, WEATHER_COLORS as C } from './charts'
import { cellsLabel, compass, daysBefore, hasTemperature, merraMissing, weatherKpis } from './weather'
import './ficha.css'

const MIN_DAYS = 30

const props = defineProps({ h3: { type: String, required: true } })
const store = useFiltersStore()
const { t } = useI18n()

const range = computed(() => {
  if (!store.end) return { start: null, end: null }
  const widest = daysBefore(store.end, MIN_DAYS - 1)
  return { start: store.start && store.start < widest ? store.start : widest, end: store.end }
})

const weather = ref(null)
const period = ref(null)
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
useLoadingBar('weather', loading)
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
    error.value = e
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
const withTemperature = computed(() => hasTemperature(weather.value?.days ?? []))
const mark = computed(() => store.day)

const kpis = computed(() => weatherKpis(weather.value?.days ?? []))

// Fire next to drying power: detections per sensor (never added up) and the daily maximum VPD.
function chartOf(title, series, options) {
  return { title, option: dailyChart(dates.value, series, options), label: dailyChartLabel(title, dates.value, series) }
}

const fireChart = computed(() => {
  const sensors = (period.value?.sensors ?? []).filter((s) => s.fire_detections > 0)
  const bars = sensors.map((s) => {
    const n = Object.fromEntries(s.daily.map((d) => [d.date, d.detections]))
    return { name: SENSOR_LABELS[s.sensor], type: 'bar', data: dates.value.map((d) => n[d] ?? 0),
             color: SENSOR_COLORS[s.sensor], digits: 0 }
  })
  return chartOf(t('weather.charts.fireDryness'), [
    ...bars,
    { name: t('weather.vpdMax'), type: 'line', axis: 1, data: series('vpd_max_kpa'), color: C.vpd, unit: 'kPa', digits: 2 },
  ], { axes: 2, mark: mark.value })
})

const temperatureChart = computed(() => chartOf(t('weather.charts.temperature'), [
  { name: t('weather.series.max'), type: 'line', data: series('t2m_max_c'), color: C.tmax, unit: '°C' },
  { name: t('weather.series.min'), type: 'line', data: series('t2m_min_c'), color: C.tmin, unit: '°C' },
  { name: t('weather.series.dew'), type: 'line', data: series('dewpoint_mean_c'), color: C.dew, unit: '°C', dashed: true },
], { scale: true, mark: mark.value }))

const humidityWindChart = computed(() => chartOf(t('weather.charts.humidityWind'), [
  { name: t('weather.kpi.rhMin'), type: 'line', data: series('rh_min_pct'), color: C.rh, unit: '%', digits: 0 },
  { name: t('weather.kpi.windMax'), type: 'line', axis: 1, data: series('wind_speed_max_ms'), color: C.wind, unit: 'm/s' },
], {
  axes: 2, mark: mark.value,
  tooltipExtra: (i) => (days.value[i].wind_dir_mean_deg != null
    ? t('weather.windFrom', { dir: `<b>${compass(days.value[i].wind_dir_mean_deg)}</b>` }) : null),
}))

const rainChart = computed(() => chartOf(t('weather.charts.rain'), [
  { name: t('weather.series.rainDay'), type: 'bar', data: series('precipitation_mm'), color: C.rain, unit: 'mm' },
  { name: t('weather.series.rain30'), type: 'line', axis: 1, data: series('precipitation_30d_mm'), color: C.rain30, unit: 'mm' },
], {
  axes: 2, mark: mark.value,
  tooltipExtra: (i) => (days.value[i].days_since_rain != null
    ? t('weather.daysWithoutRain', { n: `<b>${num(days.value[i].days_since_rain)}</b>` }) : null),
}))
</script>

<template>
  <article class="ficha">
    <header class="identity">
      <code>{{ h3 }}</code>
      <span class="muted">{{ $t('weather.climateRange', { start: date(range.start), end: date(range.end) }) }}</span>
    </header>
    <p v-if="loading && !weather" class="muted">{{ $t('common.loading') }}</p>
    <p v-else-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>
    <template v-else-if="weather">
      <WeatherCoverage :weather="weather" :days="weather.days" />
      <template v-if="hasWeather">
        <dl class="kpis">
          <div v-for="k in kpis" :key="k.label">
            <dt>
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path :d="k.icon" :fill="k.color" /></svg>
              {{ k.label }}
            </dt>
            <dd>{{ k.value }}</dd>
            <dd v-if="k.note" class="note">{{ k.note }}</dd>
          </div>
        </dl>

        <section>
          <h3>{{ fireChart.title }}</h3>
          <MiniChart :option="fireChart.option" :label="fireChart.label" height="170px" />
          <p v-if="!period" class="muted small">{{ $t('weather.noFire') }}</p>
        </section>
        <template v-if="withTemperature">
          <section v-for="c in [temperatureChart, humidityWindChart]" :key="c.title">
            <h3>{{ c.title }}</h3>
            <MiniChart :option="c.option" :label="c.label" height="150px" />
          </section>
        </template>
        <section>
          <h3>{{ rainChart.title }}</h3>
          <MiniChart :option="rainChart.option" :label="rainChart.label" height="150px" />
        </section>

        <p class="muted small">
          {{ $t('weather.nearestCell', { cells: cellsLabel(weather) }) }} {{ $t('weather.climateNote') }}
          <template v-if="withTemperature && merraMissing(days)"> {{ $t('weather.lagging') }}</template>
        </p>
      </template>
    </template>
  </article>
</template>
