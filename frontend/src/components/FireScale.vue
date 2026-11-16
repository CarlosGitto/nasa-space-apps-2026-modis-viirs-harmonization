<script setup>
// Small legend of the hexagons' color scale, following the calendar's color (store.measure):
// - hotspots / FRP: the fire ramp between its low and high values. The scale is relative to what was asked for
//   (mapShared.js densityScale), so the numbers change with the dates and the zoom; the legend says which.
// - anomalies: the violet / gray / fire ramp from ¼× to 4× the usual (the same dates of the other years).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { sig } from '../format'
import { ANOMALY_RAMP, FIRE_RAMP } from '../mapShared'

const props = defineProps({
  ticks: { type: Array, default: null }, // [low, middle, high] densities (hotspots / FRP)
  mode: { type: String, default: 'detections' }, // 'detections' | 'frp' | 'anomaly'
  noYears: { type: Boolean, default: false }, // anomalies with no other year to compare with
})
const { t } = useI18n()
const anomaly = computed(() => props.mode === 'anomaly')
const gradient = computed(() => `linear-gradient(to right, ${(anomaly.value ? ANOMALY_RAMP : FIRE_RAMP).join(', ')})`)
const ends = computed(() => (anomaly.value ? ['¼×', '4×'] : [sig(props.ticks?.[0]), sig(props.ticks?.[2])]))
const unit = computed(() => {
  if (anomaly.value) return props.noYears ? t('map.scale.noYears') : t('map.scale.anomalyUnit')
  return props.mode === 'frp' ? t('map.scale.frpUnit') : t('map.scale.unit')
})
const label = computed(() => {
  if (anomaly.value) return t('map.scale.anomalyLabel')
  const values = { low: sig(props.ticks?.[0]), mid: sig(props.ticks?.[1]), high: sig(props.ticks?.[2]) }
  return props.mode === 'frp' ? t('map.scale.frpLabel', values) : t('map.scale.label', values)
})
</script>

<template>
  <span class="scale" role="img" :aria-label="label" :title="label">
    <span aria-hidden="true">{{ ends[0] }}</span>
    <span class="bar" :style="{ background: gradient }" aria-hidden="true"></span>
    <span aria-hidden="true">{{ ends[1] }}</span>
    <span class="unit" aria-hidden="true">{{ unit }}</span>
  </span>
</template>

<style scoped>
.scale {
  display: inline-flex; align-items: center; gap: 4px; font-size: 0.6875rem; color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.bar { width: 56px; height: 8px; border-radius: 4px; border: 1px solid var(--border); }
.unit { margin-left: 2px; }
</style>
