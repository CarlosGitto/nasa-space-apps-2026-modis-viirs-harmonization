<script setup>
// Dataset ("release"), dates, basemap and zone status.
import { computed } from 'vue'
import { IMAGE_TYPES } from '../gibs'
import { useFiltersStore } from '../stores/filters'

const store = useFiltersStore()

const SENSORS = { modis: 'MODIS', viirs: 'VIIRS', harmonized: 'Harmonized' }
const PRODUCTS = { nrt: 'NRT · near real-time', sp: 'SP · historical' }

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

    <label>
      Basemap
      <select :value="store.basemap" @change="store.setBasemap($event.target.value)">
        <option value="vector">Map (OpenFreeMap)</option>
        <option v-for="(t, key) in IMAGE_TYPES" :key="key" :value="key">{{ t.label }}</option>
      </select>
    </label>
    <p v-if="store.basemap !== 'vector'" class="meta">
      <template v-if="store.imagery">
        {{ store.imagery.layer.label }} · {{ store.imagery.date }} (UTC, {{ store.day ? 'slider day' : 'end of range' }})
      </template>
      <template v-else>No satellite imagery for that date</template>
    </p>

    <div class="zone">
      <span>Zone: <b>{{ store.zone ? 'drawn' : 'whole world' }}</b></span>
      <button v-if="store.zone" @click="store.clearZone()">Clear</button>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-direction: column; gap: 10px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
select, input {
  font: inherit; font-size: 14px; color: var(--text); background: var(--bg);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px;
}
.meta { margin: -4px 0 0; font-size: 12px; color: var(--muted); }
.dates { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.zone { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.zone button {
  font: inherit; font-size: 12px; background: none; border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 8px; color: var(--text); cursor: pointer;
}
</style>