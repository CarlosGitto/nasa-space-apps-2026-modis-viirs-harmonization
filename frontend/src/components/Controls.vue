<script setup>
// Dataset ("release"), dates, basemap and zone status.
import { computed } from 'vue'
import { IMAGE_TYPES } from '../gibs'
import { WEATHER_LAYERS } from '../mapWeather'
import { useFiltersStore } from '../stores/filters'

const store = useFiltersStore()

const SENSORS = { modis: 'MODIS', viirs: 'VIIRS', harmonized: 'Harmonized' }
const PRODUCTS = { best: 'SP + NRT · continuous', sp: 'SP · science quality', nrt: 'NRT · near real-time' }

const groups = computed(() =>
  Object.entries(SENSORS).map(([sensor, label]) => ({
    label,
    options: store.datasets.filter((d) => d.sensor === sensor),
  })),
)

const fmt = (n) => n.toLocaleString('en-US')
</script>

<template>
  <section class="controls">
    <div class="view" role="group" aria-label="View">
      <button :class="{ on: store.view === 'map' }" @click="store.setView('map')">Map</button>
      <button :class="{ on: store.view === 'compare' }" @click="store.setView('compare')">Compare MODIS | VIIRS</button>
    </div>
    <label>
      Dataset
      <select :value="store.dataset" @change="store.setDataset($event.target.value)">
        <optgroup v-for="g in groups" :key="g.label" :label="g.label">
          <option v-for="d in g.options" :key="d.id" :value="d.id" :disabled="!d.available">
            {{ g.label }} {{ PRODUCTS[d.product] }}{{ d.available ? '' : ' (no data)' }}
          </option>
        </optgroup>
      </select>
    </label>
    <p v-if="store.currentDataset?.available" class="meta">
      {{ fmt(store.currentDataset.rows) }} detections ·
      {{ store.currentDataset.start_date }} → {{ store.currentDataset.end_date }}
    </p>

    <div class="dates">
      <label>
        From
        <input
          type="date" :value="store.start"
          :min="store.currentDataset?.start_date" :max="store.end"
          @change="store.setDates($event.target.value, store.end)"
        />
      </label>
      <label>
        To
        <input
          type="date" :value="store.end"
          :min="store.start" :max="store.currentDataset?.end_date"
          @change="store.setDates(store.start, $event.target.value)"
        />
      </label>
    </div>

    <label v-if="store.view === 'map'">
      Basemap
      <select :value="store.basemap" @change="store.setBasemap($event.target.value)">
        <option value="vector">Map (OpenFreeMap)</option>
        <option v-for="(t, key) in IMAGE_TYPES" :key="key" :value="key">{{ t.label }}</option>
      </select>
    </label>
    <p v-if="store.view === 'map' && store.basemap !== 'vector'" class="meta">
      <template v-if="store.imagery">
        {{ store.imagery.layer.label }} · {{ store.imagery.date }} (UTC, {{ store.day ? 'slider day' : 'end of range' }})
      </template>
      <template v-else>No satellite imagery for that date</template>
    </p>

    <fieldset v-if="store.view === 'map'" class="weather">
      <legend>Weather layers</legend>
      <label v-for="(l, id) in WEATHER_LAYERS" :key="id" class="check">
        <input type="checkbox" :checked="store.weatherLayers.includes(id)" @change="store.toggleWeatherLayer(id)" />
        {{ l.label }}
      </label>
    </fieldset>
    <p v-if="store.view === 'map' && store.weatherLayers.length" class="meta">
      NASA MERRA-2 + GPM IMERG · {{ store.imageryDate }} (UTC, {{ store.day ? 'slider day' : 'end of range' }})
    </p>

    <div class="zone">
      <span>Zone: <b>{{ store.zone ? 'drawn' : 'whole world' }}</b></span>
      <button v-if="store.zone" @click="store.clearZone()">Clear</button>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-direction: column; gap: 10px; }
.view { display: flex; gap: 2px; padding: 3px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
.view button {
  flex: 1; border: none; background: none; padding: 6px 8px; border-radius: 6px;
  font: inherit; font-size: 13px; color: var(--muted); cursor: pointer;
}
.view button.on { background: var(--accent); color: #fff; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
select, input {
  font: inherit; font-size: 14px; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px;
}
.meta { margin: -4px 0 0; font-size: 12px; color: var(--muted); }
.dates { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.weather { border: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 4px 14px; }
.weather legend { font-size: 12px; color: var(--muted); padding: 0; margin-bottom: 4px; width: 100%; }
.weather .check { flex-direction: row; align-items: center; gap: 6px; font-size: 13px; color: var(--text); cursor: pointer; }
.weather .check input { padding: 0; margin: 0; }
.zone { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.zone button {
  font: inherit; font-size: 12px; background: none; border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 8px; color: var(--text); cursor: pointer;
}
</style>