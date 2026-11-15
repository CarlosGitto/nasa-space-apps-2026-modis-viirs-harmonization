<script setup>
// Small legend of the hexagons' color scale: the fire ramp between its low and high
// values. The scale is relative to what was asked for (mapShared.js densityScale), so the
// numbers change with the dates and the zoom; the legend says which.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { sig } from '../format'
import { FIRE_RAMP } from '../mapShared'

const props = defineProps({ ticks: { type: Array, required: true } }) // [low, middle, high] densities
const { t } = useI18n()
const gradient = `linear-gradient(to right, ${FIRE_RAMP.join(', ')})`
const label = computed(() => t('map.scale.label', { low: sig(props.ticks[0]), mid: sig(props.ticks[1]), high: sig(props.ticks[2]) }))
</script>

<template>
  <span class="scale" role="img" :aria-label="label" :title="label">
    <span aria-hidden="true">{{ sig(ticks[0]) }}</span>
    <span class="bar" :style="{ background: gradient }" aria-hidden="true"></span>
    <span aria-hidden="true">{{ sig(ticks[2]) }}</span>
    <span class="unit" aria-hidden="true">{{ $t('map.scale.unit') }}</span>
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
