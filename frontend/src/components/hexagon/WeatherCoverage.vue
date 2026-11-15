<script setup>
// What to show when the selected dates have no weather (or rain but no temperature): which
// dates this place does have, and a button to jump to them.
import { computed } from 'vue'
import { date } from '../../format'
import { useFiltersStore } from '../../stores/filters'
import { coverageLabel, hasTemperature, suggestedRange } from './weather'

const props = defineProps({
  weather: { type: Object, required: true },
  days: { type: Array, required: true }, // days of the selected range with any data
})
const store = useFiltersStore()

const outside = computed(() => !props.weather.cells.some((c) => c.first_date))
// Message key (weather.coverage.*), or null when the dates have full weather.
const message = computed(() => {
  if (outside.value) return 'outside'
  if (!props.days.length) return 'noData'
  if (!hasTemperature(props.days)) return 'rainOnly'
  return null
})
const coverage = computed(() => coverageLabel(props.weather))
const suggested = computed(() => suggestedRange(props.weather))
</script>

<template>
  <div v-if="message" class="coverage" role="status">
    <p>{{ $t(`weather.coverage.${message}`) }}</p>
    <template v-if="!outside">
      <p class="muted">{{ $t('weather.coverage.loadedFor', { coverage }) }}</p>
      <button v-if="suggested" @click="store.setDates(suggested.start, suggested.end)">
        {{ $t('weather.coverage.see', { start: date(suggested.start), end: date(suggested.end) }) }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.coverage {
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start;
  padding: 8px 10px; border-radius: 6px; font-size: 0.75rem; background: var(--highlight); border-left: 3px solid var(--accent);
}
.coverage p { margin: 0; }
.coverage .muted { color: var(--muted); }
.coverage button {
  border: 1px solid var(--accent); background: var(--panel); color: var(--selected); border-radius: 6px;
  padding: 3px 10px; font: inherit; font-size: 0.75rem; cursor: pointer;
}
</style>
