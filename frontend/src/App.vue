<script setup>
import { onMounted } from 'vue'
import BurnCalendar from './components/BurnCalendar.vue'
import CompareMap from './components/CompareMap.vue'
import Controls from './components/Controls.vue'
import FireMap from './components/FireMap.vue'
import TimeSlider from './components/TimeSlider.vue'
import { useFiltersStore } from './stores/filters'

const store = useFiltersStore()
onMounted(() => store.loadDatasets())
</script>

<template>
  <div class="layout">
    <aside class="panel">
      <header>
        <h1>El Ojo de Sauron</h1>
        <p>NASA FIRMS · MODIS y VIIRS</p>
      </header>
      <p v-if="store.error" class="error">{{ store.error }}</p>
      <Controls />
      <BurnCalendar />
    </aside>
    <main class="map-area">
      <div class="map-slot">
        <!-- The main map stays alive (keeps its drawn zone); the curtain is built on demand. -->
        <FireMap v-show="store.view === 'map'" />
        <CompareMap v-if="store.view === 'compare'" />
      </div>
      <TimeSlider />
    </main>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 340px 1fr; height: 100vh; }
.panel {
  display: flex; flex-direction: column; gap: 18px; padding: 16px; overflow-y: auto;
  background: var(--panel); border-right: 1px solid var(--border);
}
header h1 { margin: 0; font-size: 18px; }
header p { margin: 2px 0 0; font-size: 12px; color: var(--muted); }
.error { margin: 0; color: var(--danger); font-size: 13px; }
.map-area { min-height: 0; display: flex; flex-direction: column; }
.map-slot { flex: 1; min-height: 0; }
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; grid-template-rows: auto 60vh; height: auto; }
  .panel { border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
