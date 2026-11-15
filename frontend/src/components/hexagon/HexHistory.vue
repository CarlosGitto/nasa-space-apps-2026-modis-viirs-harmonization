<script setup>
// Ficha · historial: the whole history of one hexagon, precomputed in gold
// (fct_hexagon_profile + fct_hexagon_monthly). One card per sensor.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHexagon, getHexagonHarmonized, getHexagonMonthly, getHexagonReliability } from '../../api'
import { useFiltersStore } from '../../stores/filters'
import { useLoadingBar } from '../../stores/loading'
import { date, errorMessage, notBuilt } from '../../format'
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import MiniChart from './MiniChart.vue'
import './ficha.css'
import {
  chartLabel, harmonizedLabel, harmonizedLine, monthLabel, monthName, monthRange, months, num, pct,
  reliabilityByYear, reliabilityLabel, reliabilityTotals, sensorBars,
} from './charts'

const props = defineProps({ h3: { type: String, required: true } })
const store = useFiltersStore()
const { t } = useI18n()
// Harmonized: MODIS units (MODIS merged, VIIRS S-NPP / k). Counts are fractional, so show a decimal.
const h = computed(() => store.hexHarmonized)
const n = (v) => num(v, h.value ? 1 : 0)

const info = ref(null)
const monthly = ref([])
const harmonized = ref({ months: [], covered_months: [] })
const harmonizedMetric = ref('frp')
const reliability = ref([])
const reliabilityMetric = ref('reliability')
const error = ref(null) // the API error (with .status), shown in the user's language
const loading = ref(false)
useLoadingBar('panel', loading)

async function load() {
  loading.value = true
  error.value = null
  try {
    ;[info.value, monthly.value] = await Promise.all([getHexagon(props.h3, h.value), getHexagonMonthly(props.h3)])
    // Optional: the spliced series may not be built yet; the rest of the ficha does not depend on it.
    // Optional too: the reliability index may not be built yet.
    reliability.value = await getHexagonReliability(props.h3).catch(() => [])
    harmonized.value = h.value
      ? await getHexagonHarmonized(props.h3).catch(() => ({ months: [], covered_months: [] }))
      : { months: [], covered_months: [] }
  } catch (e) {
    error.value = e
    info.value = null
  } finally {
    loading.value = false
  }
}
watch(() => [props.h3, h.value], load, { immediate: true })

const daysBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 86_400_000)

// Monthly detections per sensor (satellites of the same sensor added up), every month of the history.
const monthlySeries = computed(() => {
  if (h.value) {
    // Harmonized: MODIS as merged, VIIRS S-NPP divided by the cell's k, month by month: in the overlap they should match.
    const rows = harmonized.value.months
    if (!rows.length) return null
    const all = rows.map((r) => r.month).sort()
    const categories = monthRange(all[0], all.at(-1))
    const bySensor = { modis: new Array(categories.length).fill(0), viirs: new Array(categories.length).fill(0) }
    for (const r of rows) {
      const i = categories.indexOf(r.month)
      bySensor.modis[i] = r.m_detections ?? 0
      bySensor.viirs[i] = (r.v_detections ?? 0) / r.k_count
    }
    return { categories: categories.map(monthLabel), bySensor, decimals: 1 }
  }
  if (!monthly.value.length) return null
  const all = monthly.value.map((r) => r.month).sort()
  const categories = monthRange(all[0], all.at(-1))
  const bySensor = {}
  for (const r of monthly.value) {
    bySensor[r.sensor] ??= new Array(categories.length).fill(0)
    bySensor[r.sensor][categories.indexOf(r.month)] += r.fire_detections
  }
  return { categories: categories.map(monthLabel), bySensor, decimals: 0 }
})

const harmonizedChart = computed(() => (h.value ? harmonizedLine(harmonized.value, harmonizedMetric.value) : null))
// How far the k of this cell can be trusted: its own (res 6) down to the global one (res 0).
const kNote = computed(() => {
  const r = harmonized.value.months.at(-1)
  if (!r) return ''
  const source = r.k_res === 6 ? t('harm.k.own') : r.k_res === 0 ? t('harm.k.global') : t('harm.k.inherited', { res: r.k_res })
  return t('harm.k.note', { frp: num(r.k_frp, 2), count: num(r.k_count, 2), source })
})

const reliabilityChart = computed(() => reliabilityByYear(reliability.value, reliabilityMetric.value))
const reliabilityTotal = (sensor) => reliabilityTotals(reliability.value, sensor)
const reliabilityTitle = computed(() => (reliabilityMetric.value === 'reliability' ? t('harm.reliability.index') : t('harm.reliability.persistentShare')))
const harmonizedTitle = computed(() => (harmonizedMetric.value === 'frp' ? t('harm.series.frp') : t('harm.series.detections')))

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
  { label: t('ficha.recent'), value: `${n(p.last_7d)} / ${n(p.last_30d)}`, note: t('ficha.asOf', { date: date(p.data_end_date) }) },
  { label: t('ficha.detections'), value: n(p.fire_detections), note: t('ficha.since', { date: date(p.first_date) }) },
  { label: t('ficha.activeDays'), value: num(p.active_days) },
  { label: t('ficha.frpTotal'), value: mw(p.fire_frp_sum) },
  h.value
    ? { label: t('ficha.frpMax'), value: mw(p.fire_frp_max, 1) } // no p90 in MODIS units
    : { label: t('ficha.frpMaxP90'), value: `${num(p.fire_frp_max, 1)} · ${mw(p.fire_frp_p90, 1)}` },
  { label: t('ficha.frpMean'), value: mw(p.fire_frp_mean, 1) },
  // Confidence and satellites have no MODIS-units equivalent: left out when harmonized.
  ...(h.value ? [] : [{ label: t('ficha.highConf'), value: pct(p.high_conf_share) }]),
  { label: t('ficha.daytime'), value: pct(p.day_share) },
  { label: t('ficha.peakDay'), value: date(p.peak_date), note: p.peak_day_detections ? t('common.detections', { n: n(p.peak_day_detections) }) : '' },
  { label: t('ficha.peakMonth'), value: p.peak_month ? monthName(p.peak_month) : '–', note: t('ficha.peakMonthNote') },
  ...(h.value ? [] : [{ label: t('ficha.satellites'), value: num(p.satellites) }]),
]
</script>

<template>
  <article class="ficha">
    <p v-if="loading && !info" class="muted">{{ $t('common.loading') }}</p>
    <p v-else-if="notBuilt(error)" class="muted">{{ $t('harm.notBuilt') }}</p>
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
        <span v-if="h" class="muted">{{ $t('harm.historyNote') }}</span>
      </header>

      <p v-if="info.has_static_source && !h" class="static">{{ $t('ficha.static') }}</p>

      <section v-for="p in info.profiles" :key="p.sensor" class="sensor">
        <h3><span class="dot" :style="{ background: SENSOR_COLORS[p.sensor] }" aria-hidden="true"></span>{{ SENSOR_LABELS[p.sensor] }}</h3>
        <dl class="kpis">
          <div v-for="k in kpis(p)" :key="k.label">
            <dt>{{ k.label }}</dt>
            <dd>{{ k.value }}</dd>
            <dd v-if="k.note" class="note">{{ k.note }}</dd>
          </div>
        </dl>
        <p v-if="p.static_detections && !h" class="muted small">
          {{ $t('ficha.staticCount', { n: num(p.static_detections), share: pct(p.static_share) }) }}
        </p>
      </section>

      <section v-if="reliabilityChart">
        <div class="head">
          <h3>{{ $t('harm.reliability.title') }}</h3>
          <label class="metric">
            <span class="sr-only">{{ $t('harm.show') }}</span>
            <select v-model="reliabilityMetric">
              <option value="reliability">{{ $t('harm.reliability.index') }}</option>
              <option value="persistent">{{ $t('harm.reliability.persistentShare') }}</option>
            </select>
          </label>
        </div>
        <dl class="kpis">
          <template v-for="s in info.profiles" :key="s.sensor">
            <div v-if="reliabilityTotal(s.sensor)">
              <dt>{{ $t('harm.reliability.sensorMean', { sensor: SENSOR_LABELS[s.sensor] }) }}</dt>
              <dd>{{ num(reliabilityTotal(s.sensor).reliability, 2) }}</dd>
              <dd class="note">{{ $t('harm.reliability.persistentNote', { v: pct(reliabilityTotal(s.sensor).persistent) }) }}</dd>
            </div>
          </template>
        </dl>
        <MiniChart
          :option="reliabilityChart"
          :label="reliabilityLabel(reliabilityTitle, reliability, reliabilityMetric)"
          height="150px"
        />
        <p class="muted small">{{ $t('harm.reliability.explain') }}</p>
      </section>

      <section v-if="harmonizedChart">
        <div class="head">
          <h3>{{ $t('harm.series.title') }}</h3>
          <label class="metric">
            <span class="sr-only">{{ $t('harm.show') }}</span>
            <select v-model="harmonizedMetric">
              <option value="frp">{{ $t('harm.series.frp') }}</option>
              <option value="detections">{{ $t('harm.series.detections') }}</option>
            </select>
          </label>
        </div>
        <MiniChart
          :option="harmonizedChart"
          :label="harmonizedLabel(harmonizedTitle, harmonized, harmonizedMetric)"
          height="180px"
        />
        <p class="muted small">{{ $t('harm.series.explain', { k: kNote }) }}</p>
      </section>

      <section v-if="monthlySeries">
        <h3>{{ h ? $t('harm.monthlyModis') : $t('ficha.monthly') }}</h3>
        <MiniChart
          :option="sensorBars(monthlySeries.categories, monthlySeries.bySensor, { decimals: monthlySeries.decimals })"
          :label="chartLabel(h ? $t('harm.monthlyModis') : $t('ficha.monthly'), monthlySeries.categories, monthlySeries.bySensor, monthlySeries.decimals)"
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
