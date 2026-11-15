<script setup>
// Weather of one hexagon over the selected dates, compact (Ficha · período); the Clima tab
// has the full view (HexClimate). NASA POWER, asked on demand by the backend for the
// hexagon's center (cached).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHexagonWeather } from '../../api'
import { errorMessage } from '../../format'
import { useLoadingBar } from '../../stores/loading'
import MiniChart from './MiniChart.vue'
import WeatherCoverage from './WeatherCoverage.vue'
import { dailyChartLabel, weatherChart, weatherSeries } from './charts'
import { cellsLabel, hasTemperature, merraMissing, weatherKpis } from './weather'
import './ficha.css'

const props = defineProps({
  h3: { type: String, required: true },
  start: { type: String, default: null },
  end: { type: String, default: null },
})
const { t } = useI18n()

const weather = ref(null)
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
useLoadingBar('weather', loading)
let controller = null

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = null
  try {
    weather.value = await getHexagonWeather(props.h3, { start: props.start, end: props.end }, controller.signal)
  } catch (e) {
    if (e.name === 'AbortError') return
    error.value = e
    weather.value = null
  }
  loading.value = false
}
watch(() => [props.h3, props.start, props.end], load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

const days = computed(() => weather.value?.days ?? [])
const kpis = computed(() => weatherKpis(days.value))
const cells = computed(() => cellsLabel(weather.value))
const lagging = computed(() => hasTemperature(days.value) && merraMissing(days.value))
const chart = computed(() => (days.value.length ? weatherChart(days.value) : null))
const chartText = computed(() =>
  dailyChartLabel(t('weather.title'), days.value.map((d) => d.date), weatherSeries(days.value)))
</script>

<template>
  <section class="weather">
    <h3>{{ $t('weather.title') }} <span class="muted">· NASA POWER</span></h3>
    <p v-if="loading && !weather" class="muted">{{ $t('common.loading') }}</p>
    <p v-else-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>
    <template v-else-if="weather">
      <WeatherCoverage :weather="weather" :days="days" />
      <template v-if="days.length">
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
        <MiniChart v-if="chart" :option="chart" :label="chartText" height="180px" />
        <p class="muted small">
          {{ $t('weather.nearestCell', { cells }) }} {{ $t('weather.vpdNote') }}
          <template v-if="lagging"> {{ $t('weather.lagging') }}</template>
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.weather h3 .muted { font-weight: 400; }
</style>
