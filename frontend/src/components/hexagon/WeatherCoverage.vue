<script setup>
// What to show when the selected dates have no weather (or rain but no MERRA-2): which
// dates this place does have, and a button to jump to them.
import { computed } from 'vue'
import { useFiltersStore } from '../../stores/filters'
import { coverageLabel, hasTemperature, suggestedRange } from './weather'

const props = defineProps({
  weather: { type: Object, required: true },
  days: { type: Array, required: true }, // days of the selected range with any data
})
const store = useFiltersStore()

const outside = computed(() => !props.weather.cells.some((c) => c.first_date))
const message = computed(() => {
  if (outside.value) return 'Este lugar está fuera del área con datos meteorológicos cargados.'
  if (!props.days.length) return 'No hay datos meteorológicos para estas fechas.'
  if (!hasTemperature(props.days)) return 'Estas fechas tienen lluvia, pero todavía no temperatura, humedad ni viento.'
  return null
})
const coverage = computed(() => coverageLabel(props.weather))
const suggested = computed(() => suggestedRange(props.weather))
</script>

<template>
  <div v-if="message" class="coverage">
    <p>{{ message }}</p>
    <template v-if="!outside">
      <p class="muted">Datos cargados para este lugar: {{ coverage }}.</p>
      <button v-if="suggested" @click="store.setDates(suggested.start, suggested.end)">
        Ver {{ suggested.start }} → {{ suggested.end }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.coverage {
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start;
  padding: 8px 10px; border-radius: 6px; font-size: 12px; background: #eff6ff; border-left: 3px solid #2563eb;
}
.coverage p { margin: 0; }
.coverage .muted { color: var(--muted); }
.coverage button {
  border: 1px solid #2563eb; background: #fff; color: #1d4ed8; border-radius: 6px;
  padding: 3px 10px; font: inherit; font-size: 12px; cursor: pointer;
}
</style>
