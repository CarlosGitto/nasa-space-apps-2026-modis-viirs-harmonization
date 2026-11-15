<script setup>
// Dataset ("release") for the raw values and the points up close, exact dates, basemap, weather layers and zone
// status. Folded under the fire calendar, which picks the period and the region.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { date, num } from '../format'
import { IMAGE_TYPES } from '../gibs'
import { WEATHER_LAYERS } from '../mapWeather'
import { useFiltersStore } from '../stores/filters'

const store = useFiltersStore()
const { t } = useI18n()

// Sensor names are technical (not translated), except "harmonized".
const sensorLabel = (sensor) => ({ modis: 'MODIS', viirs: 'VIIRS' })[sensor] ?? t(`controls.sensors.${sensor}`)

// modis_harmonized / viirs_harmonized are the harmonized layers of the Compare view and the calendar; they would
// show up here with the same label as modis_best / viirs_best (same sensor and product, fewer detections).
const selectable = (d) => !d.id.endsWith('_harmonized')

// The external "Harmonized" group (datasets that a harmonization team drops into the lake) only shows once one of
// them has data; empty, it is just two "(no data)" entries.
const groups = computed(() =>
  ['modis', 'viirs', 'harmonized']
    .map((sensor) => ({
      sensor,
      label: sensorLabel(sensor),
      options: store.datasets.filter((d) => d.sensor === sensor && selectable(d)),
    }))
    .filter((g) => g.sensor !== 'harmonized' || g.options.some((d) => d.available)),
)
</script>

<template>
  <section class="controls">
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

    <fieldset v-if="store.view === 'map'" class="weather">
      <legend>{{ $t('weather.layersTitle') }}</legend>
      <label v-for="(l, id) in WEATHER_LAYERS" :key="id" class="check">
        <input type="checkbox" :checked="store.weatherLayers.includes(id)" @change="store.toggleWeatherLayer(id)" />
        {{ l.label }}
      </label>
    </fieldset>
    <div v-if="store.view === 'map' && store.weatherLayers.length" class="scope" role="group" :aria-label="$t('weather.scope')">
      <button :aria-pressed="store.weatherScope === 'fires'" :class="{ on: store.weatherScope === 'fires' }" @click="store.setWeatherScope('fires')">
        {{ $t('weather.scopeFires') }}
      </button>
      <button :aria-pressed="store.weatherScope === 'all'" :class="{ on: store.weatherScope === 'all' }" @click="store.setWeatherScope('all')">
        {{ $t('weather.scopeAll') }}
      </button>
    </div>

    <div class="zone">
      <span>{{ $t('controls.zone') }} <b>{{ (store.zone ? $t('controls.zoneDrawn') : $t('controls.zoneWorld')) }}</b></span>
      <button v-if="store.zone" @click="store.clearZone()">{{ $t('controls.clear') }}</button>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-direction: column; gap: 10px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 0.75rem; color: var(--muted); }
select, input {
  font: inherit; font-size: 0.875rem; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px;
}
.meta { margin: -4px 0 0; font-size: 0.75rem; color: var(--muted); }
.dates { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.weather { border: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 4px 14px; }
.weather legend { font-size: 0.75rem; color: var(--muted); padding: 0; margin-bottom: 4px; width: 100%; }
.weather .check { flex-direction: row; align-items: center; gap: 6px; font-size: 0.8125rem; color: var(--text); cursor: pointer; }
.weather .check input { padding: 0; margin: 0; }
.scope { display: flex; gap: 2px; padding: 2px; border: 1px solid var(--border); border-radius: 6px; margin-top: -4px; }
.scope button {
  flex: 1; border: none; background: none; padding: 4px 6px; border-radius: 4px;
  font: inherit; font-size: 0.75rem; color: var(--muted); cursor: pointer;
}
.scope button.on { background: var(--bg); color: var(--text); font-weight: 600; box-shadow: 0 0 0 1px var(--border); }
.zone { display: flex; justify-content: space-between; align-items: center; font-size: 0.8125rem; }
.zone button {
  font: inherit; font-size: 0.75rem; background: none; border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 8px; color: var(--text); cursor: pointer;
}
</style>