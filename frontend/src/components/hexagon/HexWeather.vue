<script setup>
// Weather of one hexagon over the selected dates, compact (Ficha · período); the Clima tab
// has the full view (HexClimate). NASA Earthdata: MERRA-2 + GPM IMERG, per grid cell: the
// backend takes the cell nearest to the hexagon's center.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getHexagonWeather } from '../../api'
import MiniChart from './MiniChart.vue'
import WeatherCoverage from './WeatherCoverage.vue'
import { weatherChart } from './charts'
import { cellsLabel, hasTemperature, merraMissing, weatherKpis } from './weather'
import './ficha.css'

const props = defineProps({
  h3: { type: String, required: true },
  start: { type: String, default: null },
  end: { type: String, default: null },
})

const weather = ref(null)
const error = ref(null)
const loading = ref(false)
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
    error.value = e.message
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
</script>

<template>
  <section class="weather">
    <h4>Clima <span class="muted">· NASA MERRA-2 + GPM IMERG</span></h4>
    <p v-if="loading && !weather" class="muted">Cargando…</p>
    <p v-else-if="error?.startsWith('no weather')" class="muted">Todavía no hay datos meteorológicos cargados.</p>
    <p v-else-if="error" class="error">{{ error }}</p>
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
        <MiniChart v-if="chart" :option="chart" height="180px" />
        <p class="muted small">
          Celda más cercana: {{ cells }}. Días UTC; VPD = déficit de presión de vapor (cuánto seca el aire).
          <template v-if="lagging"> Los últimos días todavía no tienen MERRA-2 (se publica con semanas de demora).</template>
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.weather h4 .muted { font-weight: 400; }
</style>
