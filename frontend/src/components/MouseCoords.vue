<script setup>
// Latitude and longitude under the mouse, tiny, bottom right of the map (above the
// attribution). Pointer-only information: hidden from screen readers, which would
// otherwise hear it change on every mouse move.
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { num } from '../format'

const props = defineProps({ maps: { type: Array, required: true } }) // MapLibre maps (the curtain has two)
const { t } = useI18n()
const at = ref(null) // { lat, lon } or null when the mouse is off the map

const move = (e) => { at.value = { lat: e.lngLat.lat, lon: e.lngLat.lng } }
const out = () => { at.value = null }
let attached = []
function attach(maps) {
  for (const m of attached) { m.off('mousemove', move); m.off('mouseout', out) }
  attached = [...maps]
  for (const m of attached) { m.on('mousemove', move); m.on('mouseout', out) }
}
watch(() => props.maps, attach, { immediate: true })
onBeforeUnmount(() => attach([]))

const lat = (v) => `${num(Math.abs(v), 4)}° ${v < 0 ? t('map.hemi.s') : t('map.hemi.n')}`
const lon = (v) => `${num(Math.abs(v), 4)}° ${v < 0 ? t('map.hemi.w') : t('map.hemi.e')}`
</script>

<template>
  <div v-if="at" class="coords" aria-hidden="true">{{ lat(at.lat) }} · {{ lon(at.lon) }}</div>
</template>

<style scoped>
.coords {
  position: absolute; right: 10px; bottom: 30px; z-index: 2; pointer-events: none; /* just above the attribution */
  padding: 1px 6px; border-radius: 4px; font-size: 0.6875rem; color: var(--text);
  background: color-mix(in srgb, var(--panel) 80%, transparent); font-variant-numeric: tabular-nums;
}
</style>
