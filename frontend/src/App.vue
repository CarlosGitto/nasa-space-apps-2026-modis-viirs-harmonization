<script setup>
import { computed, onMounted } from 'vue'
import A11yMenu from './components/A11yMenu.vue'
import BurnCalendar from './components/BurnCalendar.vue'
import CompareMap from './components/CompareMap.vue'
import Controls from './components/Controls.vue'
import FireMap from './components/FireMap.vue'
import LanguageSwitch from './components/LanguageSwitch.vue'
import LoadingBar from './components/LoadingBar.vue'
import TimeSlider from './components/TimeSlider.vue'
import HexagonPanel from './components/hexagon/HexagonPanel.vue'
import { useFiltersStore } from './stores/filters'
import { usePrefsStore } from './stores/prefs'

const store = useFiltersStore()
usePrefsStore() // applies the saved language and accessibility options to <html>
const panelOpen = computed(() => store.view === 'map' && store.selectedHexes.length > 0)
onMounted(() => store.loadDatasets())
</script>

<template>
  <LoadingBar />
  <a class="skip-link" href="#map">{{ $t('app.skipToMap') }}</a>
  <div class="layout" :class="{ 'with-hex': panelOpen }">
    <aside class="panel" :aria-label="$t('app.sidebar')">
      <header>
        <div class="brand">
          <h1>{{ $t('app.title') }}</h1>
          <p>{{ $t('app.subtitle') }}</p>
        </div>
        <div class="prefs">
          <LanguageSwitch />
          <A11yMenu />
        </div>
      </header>
      <p v-if="store.error" class="error" role="alert">{{ $t('app.backendDown', { error: store.error }) }}</p>
      <Controls />
      <BurnCalendar />
    </aside>
    <main id="map" class="map-area" tabindex="-1" :aria-label="$t('app.mapArea')">
      <div class="map-slot">
        <!-- The main map stays alive (keeps its drawn zone); the curtain is built on demand. -->
        <FireMap v-show="store.view === 'map'" />
        <CompareMap v-if="store.view === 'compare'" />
      </div>
      <TimeSlider />
    </main>
    <HexagonPanel v-if="panelOpen" />
  </div>
</template>

<style scoped>
/* Widths in rem: with a bigger text size the side panels grow with it. */
.layout { display: grid; grid-template-columns: 21.25rem 1fr; height: 100vh; }
.layout.with-hex { grid-template-columns: 21.25rem 1fr minmax(23.75rem, 34vw); }
.panel {
  display: flex; flex-direction: column; gap: 18px; padding: 16px; overflow-y: auto;
  background: var(--panel); border-right: 1px solid var(--border);
}
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
header h1 { margin: 0; font-size: 1.125rem; }
header p { margin: 2px 0 0; font-size: 0.75rem; color: var(--muted); }
.prefs { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }
.error { margin: 0; color: var(--danger); font-size: 0.8125rem; }
.map-area { min-height: 0; display: flex; flex-direction: column; container: map-area / inline-size; }
.map-area:focus { outline: none; }
.map-slot { flex: 1; min-height: 0; }
@media (max-width: 760px) {
  .layout, .layout.with-hex { grid-template-columns: 1fr; grid-template-rows: auto 60vh; height: auto; }
  .panel { border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
