<script setup>
// Ficha · historial: the whole history of one hexagon, precomputed in gold
// (fct_hexagon_profile + fct_hexagon_monthly). One card per sensor.
import { computed, ref, watch } from 'vue'
import { getHexagon, getHexagonHarmonized, getHexagonMonthly } from '../../api'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import MiniChart from './MiniChart.vue'
import './ficha.css'
import { MONTHS, harmonizedLine, monthLabel, monthRange, num, pct, sensorBars } from './charts'

const props = defineProps({ h3: { type: String, required: true } })

const info = ref(null)
const monthly = ref([])
const harmonized = ref({ months: [], covered_months: [] })
const harmonizedMetric = ref('frp')
const error = ref(null)
const loading = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    ;[info.value, monthly.value] = await Promise.all([getHexagon(props.h3), getHexagonMonthly(props.h3)])
    // Optional: the spliced series may not be built yet; the rest of the ficha does not depend on it.
    harmonized.value = await getHexagonHarmonized(props.h3).catch(() => ({ months: [], covered_months: [] }))
  } catch (e) {
    error.value = e.message
    info.value = null
  } finally {
    loading.value = false
  }
}
watch(() => props.h3, load, { immediate: true })

const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86_400_000)

// Monthly detections per sensor (satellites of the same sensor added up), every month of the history.
const monthlyChart = computed(() => {
  if (!monthly.value.length) return null
  const months = monthly.value.map((r) => r.month).sort()
  const categories = monthRange(months[0], months.at(-1))
  const bySensor = {}
  for (const r of monthly.value) {
    bySensor[r.sensor] ??= new Array(categories.length).fill(0)
    bySensor[r.sensor][categories.indexOf(r.month)] += r.fire_detections
  }
  return sensorBars(categories.map(monthLabel), bySensor)
})

const harmonizedChart = computed(() => harmonizedLine(harmonized.value, harmonizedMetric.value))
// How far the k of this cell can be trusted: its own (res 6) down to the global one (res 0).
const kNote = computed(() => {
  const r = harmonized.value.months.at(-1)
  if (!r) return ''
  const own = r.k_res === 6 ? 'propio de la celda' : r.k_res === 0 ? 'global' : `heredado de la celda de resolución ${r.k_res}`
  return `k (VIIRS / MODIS): FRP ${num(r.k_frp, 2)}, detecciones ${num(r.k_count, 2)} · ${own}`
})

const climatologyChart = computed(() => {
  const profiles = info.value?.profiles ?? []
  if (!profiles.some((p) => p.fire_detections > 0)) return null
  return sensorBars(MONTHS, Object.fromEntries(profiles.map((p) => [p.sensor, p.climatology])), { labelInterval: 0 })
})

const kpis = (p) => [
  {
    label: 'Última detección',
    value: p.last_date ?? '–',
    note: p.last_date && p.data_end_date ? `${daysBetween(p.last_date, p.data_end_date)} días antes del último dato` : '',
  },
  { label: 'Últimos 7 / 30 días', value: `${num(p.last_7d)} / ${num(p.last_30d)}`, note: `al ${p.data_end_date ?? '–'}` },
  { label: 'Detecciones', value: num(p.fire_detections), note: `desde ${p.first_date ?? '–'}` },
  { label: 'Días con fuego', value: num(p.active_days) },
  { label: 'FRP total', value: `${num(p.fire_frp_sum)} MW` },
  { label: 'FRP máx · p90', value: `${num(p.fire_frp_max, 1)} · ${num(p.fire_frp_p90, 1)} MW` },
  { label: 'FRP medio', value: `${num(p.fire_frp_mean, 1)} MW` },
  { label: 'Alta confianza', value: pct(p.high_conf_share) },
  { label: 'Diurnas', value: pct(p.day_share) },
  { label: 'Día pico', value: p.peak_date ?? '–', note: p.peak_day_detections ? `${num(p.peak_day_detections)} detecciones` : '' },
  { label: 'Mes pico', value: p.peak_month ? MONTHS[p.peak_month - 1] : '–', note: 'del año, según la climatología' },
  { label: 'Satélites', value: num(p.satellites) },
]
</script>

<template>
  <article class="ficha">
    <p v-if="loading && !info" class="muted">Cargando…</p>
    <p v-else-if="error?.startsWith('no detections')" class="muted">Sin detecciones en toda la historia de este hexágono.</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <template v-else-if="info">
      <header class="identity">
        <code>{{ info.h3 }}</code>
        <span class="muted">
          {{ num(info.area_km2, 1) }} km² · centro {{ num(info.center[1], 4) }}, {{ num(info.center[0], 4) }} ·
          datos {{ info.first_date }} → {{ info.last_date }}
        </span>
      </header>

      <p v-if="info.has_static_source" class="static">
        ⚠ Fuente fija de calor (industria, antorcha de gas, volcán…): esas detecciones se cuentan aparte, no como incendio.
      </p>

      <section v-for="p in info.profiles" :key="p.sensor" class="sensor">
        <h4><span class="dot" :style="{ background: SENSOR_COLORS[p.sensor] }"></span>{{ SENSOR_LABELS[p.sensor] }}</h4>
        <dl class="kpis">
          <div v-for="k in kpis(p)" :key="k.label">
            <dt>{{ k.label }}</dt>
            <dd>{{ k.value }}</dd>
            <dd v-if="k.note" class="note">{{ k.note }}</dd>
          </div>
        </dl>
        <p v-if="p.static_detections" class="muted small">
          + {{ num(p.static_detections) }} detecciones de fuente fija ({{ pct(p.static_share) }} del total)
        </p>
      </section>

      <section v-if="harmonizedChart">
        <h4>
          Serie continua MODIS + VIIRS
          <select v-model="harmonizedMetric" class="metric">
            <option value="frp">FRP (MW)</option>
            <option value="detections">Detecciones</option>
          </select>
        </h4>
        <MiniChart :option="harmonizedChart" height="180px" />
        <p class="muted small">
          Meses sin datos descargados quedan en blanco (no son cero). Antes de feb 2012, MODIS (sin duplicados de bow-tie); después, VIIRS S-NPP ÷ k, en unidades MODIS.
          {{ kNote }}. El valor mensual es ruidoso: guíate por la media de 12 meses.
        </p>
      </section>
      <section v-if="monthlyChart">
        <h4>Detecciones por mes</h4>
        <MiniChart :option="monthlyChart" />
      </section>
      <section v-if="climatologyChart">
        <h4>Climatología · ¿cuándo suele quemarse?</h4>
        <MiniChart :option="climatologyChart" height="140px" />
      </section>
    </template>
  </article>
</template>
