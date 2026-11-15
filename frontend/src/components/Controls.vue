<script setup>
// Dataset ("release"), dates, basemap and zone status.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { date, num } from '../format'
import { IMAGE_TYPES } from '../gibs'
import { useFiltersStore } from '../stores/filters'

const store = useFiltersStore()
const { t } = useI18n()

// Sensor names are technical (not translated), except "harmonized".
const sensorLabel = (sensor) => ({ modis: 'MODIS', viirs: 'VIIRS' })[sensor] ?? t(`controls.sensors.${sensor}`)

const groups = computed(() =>
  ['modis', 'viirs', 'harmonized'].map((sensor) => ({
    label: sensorLabel(sensor),
    options: store.datasets.filter((d) => d.sensor === sensor),
  })),
)
</script>

<template>
  <section class="controls">
    <div class="view" role="group" :aria-label="$t('controls.view')">
      <button :aria-pressed="store.view === 'map'" :class="{ on: store.view === 'map' }" @click="store.setView('map')">
        {{ $t('controls.map') }}
      </button>
      <button :aria-pressed="store.view === 'compare'" :class="{ on: store.view === 'compare' }" @click="store.setView('compare')">
        {{ $t('controls.compare') }}
      </button>
    </div>
    <label>
      {{ $t('controls.dataset') }}
      <select :value="store.dataset" @change="store.setDataset($event.target.value)">
        <optgroup v-for="g in groups" :key="g.label" :label="g.label">
          <option v-for="d in g.options" :key="d.id" :value="d.id" :disabled="!d.available">
            {{ g.label }} {{ $t(`controls.products.${d.product}`) }}{{ d.available ? '' : $t('controls.noDataSuffix') }}
          </option>
        </optgroup>
      </select>
    </label>
    <p v-if="store.currentDataset?.available" class="meta">
      {{ $t('controls.meta', {
        n: num(store.currentDataset.rows), start: date(store.currentDataset.start_date), end: date(store.currentDataset.end_date),
      }) }}
    </p>

    <div class="dates">
      <label>
        {{ $t('controls.from') }}
        <input
          type="date" :value="store.start"
          :min="store.currentDataset?.start_date" :max="store.end"
          @change="store.setDates($event.target.value, store.end)"
        />
      </label>
      <label>
        {{ $t('controls.to') }}
        <input
          type="date" :value="store.end"
          :min="store.start" :max="store.currentDataset?.end_date"
          @change="store.setDates(store.start, $event.target.value)"
        />
      </label>
    </div>

    <label v-if="store.view === 'map'">
      {{ $t('controls.basemap') }}
      <select :value="store.basemap" @change="store.setBasemap($event.target.value)">
        <option value="vector">{{ $t('controls.vectorMap') }}</option>
        <option v-for="(type, key) in IMAGE_TYPES" :key="key" :value="key">{{ $t(type.labelKey) }}</option>
      </select>
    </label>
    <p v-if="store.view === 'map' && store.basemap !== 'vector'" class="meta" aria-live="polite">
      <template v-if="store.imagery">
        {{ $t('controls.imagery', {
          layer: store.imagery.layer.label, date: date(store.imagery.date),
          which: (store.day ? $t('controls.sliderDay') : $t('controls.endOfRange')),
        }) }}
      </template>
      <template v-else>{{ $t('controls.noImagery') }}</template>
    </p>

    <div class="zone">
      <span>{{ $t('controls.zone') }} <b>{{ (store.zone ? $t('controls.zoneDrawn') : $t('controls.zoneWorld')) }}</b></span>
      <button v-if="store.zone" @click="store.clearZone()">{{ $t('controls.clear') }}</button>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-direction: column; gap: 10px; }
.view { display: flex; gap: 2px; padding: 3px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
.view button {
  flex: 1; border: none; background: none; padding: 6px 8px; border-radius: 6px;
  font: inherit; font-size: 0.8125rem; color: var(--muted); cursor: pointer;
}
.view button.on { background: var(--accent); color: var(--on-accent); }
label { display: flex; flex-direction: column; gap: 4px; font-size: 0.75rem; color: var(--muted); }
select, input {
  font: inherit; font-size: 0.875rem; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px;
}
.meta { margin: -4px 0 0; font-size: 0.75rem; color: var(--muted); }
.dates { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.zone { display: flex; justify-content: space-between; align-items: center; font-size: 0.8125rem; }
.zone button {
  font: inherit; font-size: 0.75rem; background: none; border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 8px; color: var(--text); cursor: pointer;
}
</style>