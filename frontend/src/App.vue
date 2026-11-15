<script setup>
import { computed, onMounted } from 'vue'
import { datasetName, date } from './format'
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
const prefs = usePrefsStore() // applies the saved language and accessibility options to <html>
const collapsed = computed(() => prefs.sidebarCollapsed)
const panelOpen = computed(() => store.view === 'map' && store.selectedHexes.length > 0)
onMounted(() => store.loadDatasets())
</script>

<template>
  <LoadingBar />
  <a class="skip-link" href="#map">{{ $t('app.skipToMap') }}</a>
  <div class="layout" :class="{ 'with-hex': panelOpen, folded: collapsed }">
    <aside class="panel" :class="{ collapsed }" :aria-label="$t('app.sidebar')">
      <!-- Fold the panel to a thin rail (more map); the rail still says which data the map shows. -->
      <button
        type="button" class="fold" :aria-expanded="!collapsed" aria-controls="sidebar-content"
        @click="prefs.sidebarCollapsed = !collapsed"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" :class="{ flip: collapsed }">
          <path d="M15.4 7.4 14 6l-6 6 6 6 1.4-1.4L10.8 12z" fill="currentColor" />
        </svg>
        <span class="fold-text">{{ collapsed ? $t('app.showPanel') : $t('app.hidePanel') }}</span>
      </button>
      <h1 v-if="collapsed" class="sr-only">{{ $t('app.title') }}</h1>
      <p v-if="collapsed" class="rail">
        {{ $t('app.railSummary', { dataset: datasetName(store.currentDataset), start: date(store.start), end: date(store.end) }) }}
      </p>
      <div v-show="!collapsed" id="sidebar-content" class="panel-content">
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
      </div>
    </aside>
    <main id="map" class="map-area" tabindex="-1" :aria-label="$t('app.mapArea')">
      <div class="map-slot">
        <!-- The main map stays alive (keeps its drawn zone); the curtain is built on demand. -->
        <FireMap v-show="store.view === 'map'" />
        <CompareMap v-if="store.view === 'compare'" />
      </div>
      <!-- Not in the split view: each side there is a whole period. -->
      <TimeSlider v-show="store.view === 'map'" />
    </main>
    <HexagonPanel v-if="panelOpen" />
  </div>
</template>

<style scoped>
/* Widths in rem: with a bigger text size the side panels grow with it. */
.layout {
  display: grid; grid-template-columns: 21.25rem 1fr; height: 100vh;
  transition: grid-template-columns 0.2s ease;
}
.layout.with-hex { grid-template-columns: 21.25rem 1fr minmax(23.75rem, 34vw); }
.layout.folded { grid-template-columns: 2.75rem 1fr; }
.layout.folded.with-hex { grid-template-columns: 2.75rem 1fr minmax(23.75rem, 34vw); }
:global(html[data-reduce-motion]) .layout { transition: none; }
.panel {
  position: relative; z-index: 3; min-height: 0; display: flex; flex-direction: column;
  background: var(--panel); border-right: 1px solid var(--border);
}
.panel-content { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 18px; padding: 16px; overflow-y: auto; }
/* The fold handle sits on the panel's edge, half over the map, so it's always in sight. */
.fold {
  position: absolute; top: 50%; right: -14px; width: 28px; height: 52px; margin-top: -26px; padding: 0;
  display: flex; align-items: center; justify-content: center; font: inherit; font-size: 1rem; font-weight: 700;
  color: var(--text); background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  box-shadow: var(--shadow); cursor: pointer;
}
.fold:hover { background: var(--highlight); }
.fold svg.flip { transform: scaleX(-1); }
/* Its name shows next to it on hover and keyboard focus (and is always read by screen readers). */
.fold-text {
  position: absolute; left: calc(100% + 6px); white-space: nowrap; padding: 3px 8px; border-radius: 6px;
  font-size: 0.75rem; font-weight: 500; background: var(--text); color: var(--panel);
  clip-path: inset(50%); width: 1px; height: 1px; overflow: hidden;
}
.fold:hover .fold-text, .fold:focus-visible .fold-text { clip-path: none; width: auto; height: auto; overflow: visible; }
.rail {
  margin: 16px auto; writing-mode: vertical-rl; transform: rotate(180deg); font-size: 0.75rem; color: var(--muted);
  white-space: nowrap; overflow: hidden;
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
  .fold { position: static; width: 100%; height: auto; margin: 0; padding: 6px; border-radius: 0; box-shadow: none; gap: 6px; }
  .fold-text { position: static; clip-path: none; width: auto; height: auto; overflow: visible; background: none; color: inherit; }
  .rail { writing-mode: horizontal-tb; transform: none; margin: 4px 12px 8px; white-space: normal; }
}
</style>
