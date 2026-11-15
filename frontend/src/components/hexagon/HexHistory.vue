<script setup>
// Ficha · historial: the whole history of one hexagon, precomputed in gold
// (fct_hexagon_profile + fct_hexagon_monthly). One card per sensor.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHexagon, getHexagonMonthly } from '../../api'
import { useLoadingBar } from '../../stores/loading'
import { date, errorMessage } from '../../format'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import MiniChart from './MiniChart.vue'
import './ficha.css'
import { chartLabel, monthLabel, monthName, monthRange, months, num, pct, sensorBars } from './charts'

const props = defineProps({ h3: { type: String, required: true } })
const { t } = useI18n()

const info = ref(null)
const monthly = ref([])
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
useLoadingBar('panel', loading)

async function load() {
  loading.value = true
  error.value = null
  try {
    ;[info.value, monthly.value] = await Promise.all([getHexagon(props.h3), getHexagonMonthly(props.h3)])
  } catch (e) {
    error.value = e
    info.value = null
  } finally {
    loading.value = false
  }
}
watch(() => props.h3, load, { immediate: true })

const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86_400_000)

// Monthly detections per sensor (satellites of the same sensor added up), every month of the history.
const monthlySeries = computed(() => {
  if (!monthly.value.length) return null
  const all = monthly.value.map((r) => r.month).sort()
  const categories = monthRange(all[0], all.at(-1))
  const bySensor = {}
  for (const r of monthly.value) {
    bySensor[r.sensor] ??= new Array(categories.length).fill(0)
    bySensor[r.sensor][categories.indexOf(r.month)] += r.fire_detections
  }
  return { categories: categories.map(monthLabel), bySensor }
})

const climatologySeries = computed(() => {
  const profiles = info.value?.profiles ?? []
  if (!profiles.some((p) => p.fire_detections > 0)) return null
  return { categories: months(), bySensor: Object.fromEntries(profiles.map((p) => [p.sensor, p.climatology])) }
})

const mw = (v, digits = 0) => t('common.mw', { v: num(v, digits) })

const kpis = (p) => [
  {
    label: t('ficha.lastDetection'),
    value: date(p.last_date),
    note: p.last_date && p.data_end_date ? t('ficha.daysBeforeEnd', { n: num(daysBetween(p.last_date, p.data_end_date)) }) : '',
  },
  { label: t('ficha.recent'), value: `${num(p.last_7d)} / ${num(p.last_30d)}`, note: t('ficha.asOf', { date: date(p.data_end_date) }) },
  { label: t('ficha.detections'), value: num(p.fire_detections), note: t('ficha.since', { date: date(p.first_date) }) },
  { label: t('ficha.activeDays'), value: num(p.active_days) },
  { label: t('ficha.frpTotal'), value: mw(p.fire_frp_sum) },
  { label: t('ficha.frpMaxP90'), value: `${num(p.fire_frp_max, 1)} · ${mw(p.fire_frp_p90, 1)}` },
  { label: t('ficha.frpMean'), value: mw(p.fire_frp_mean, 1) },
  { label: t('ficha.highConf'), value: pct(p.high_conf_share) },
  { label: t('ficha.daytime'), value: pct(p.day_share) },
  { label: t('ficha.peakDay'), value: date(p.peak_date), note: p.peak_day_detections ? t('common.detections', { n: num(p.peak_day_detections) }) : '' },
  { label: t('ficha.peakMonth'), value: p.peak_month ? monthName(p.peak_month) : '–', note: t('ficha.peakMonthNote') },
  { label: t('ficha.satellites'), value: num(p.satellites) },
]
</script>

<template>
  <article class="ficha">
    <p v-if="loading && !info" class="muted">{{ $t('common.loading') }}</p>
    <p v-else-if="error?.status === 404" class="muted">{{ $t('ficha.noHistory') }}</p>
    <p v-else-if="error" class="error" role="alert">{{ errorMessage(error) }}</p>
    <template v-else-if="info">
      <header class="identity">
        <code>{{ info.h3 }}</code>
        <span class="muted">
          {{ $t('ficha.identity', {
            area: num(info.area_km2, 1), lat: num(info.center[1], 4), lon: num(info.center[0], 4),
            first: date(info.first_date), last: date(info.last_date),
          }) }}
        </span>
      </header>

      <p v-if="info.has_static_source" class="static">{{ $t('ficha.static') }}</p>

      <section v-for="p in info.profiles" :key="p.sensor" class="sensor">
        <h3><span class="dot" :style="{ background: SENSOR_COLORS[p.sensor] }" aria-hidden="true"></span>{{ SENSOR_LABELS[p.sensor] }}</h3>
        <dl class="kpis">
          <div v-for="k in kpis(p)" :key="k.label">
            <dt>{{ k.label }}</dt>
            <dd>{{ k.value }}</dd>
            <dd v-if="k.note" class="note">{{ k.note }}</dd>
          </div>
        </dl>
        <p v-if="p.static_detections" class="muted small">
          {{ $t('ficha.staticCount', { n: num(p.static_detections), share: pct(p.static_share) }) }}
        </p>
      </section>

      <section v-if="monthlySeries">
        <h3>{{ $t('ficha.monthly') }}</h3>
        <MiniChart
          :option="sensorBars(monthlySeries.categories, monthlySeries.bySensor)"
          :label="chartLabel($t('ficha.monthly'), monthlySeries.categories, monthlySeries.bySensor)"
        />
      </section>
      <section v-if="climatologySeries">
        <h3>{{ $t('ficha.climatology') }}</h3>
        <MiniChart
          :option="sensorBars(climatologySeries.categories, climatologySeries.bySensor, { labelInterval: 0 })"
          :label="chartLabel($t('ficha.climatology'), climatologySeries.categories, climatologySeries.bySensor)"
          height="140px"
        />
      </section>
    </template>
  </article>
</template>
