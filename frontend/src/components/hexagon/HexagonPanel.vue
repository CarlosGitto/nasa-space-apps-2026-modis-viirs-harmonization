<script setup>
// Right-side panel for the selected res-6 hexagons: observations table, the two fichas and
// the weather (Clima).
// Several hexagons: "Detalle" stacks one ficha per hexagon (a few) or lists them busiest
// first with their ficha on click (a drawn area); "Agrupado" is WIP (how to add
// hexagons up is not defined yet).
//
// Keyboard: tabs follow the WAI-ARIA tabs pattern (arrows, Home, End); opening the
// panel moves the focus to its title, closing it (✕ or Esc) returns it where it was.
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { num } from '../../format'
import { useFiltersStore } from '../../stores/filters'
import HexClimate from './HexClimate.vue'
import HexHistory from './HexHistory.vue'
import HexObservations from './HexObservations.vue'
import HexPeriod from './HexPeriod.vue'
import HexRanking from './HexRanking.vue'

const store = useFiltersStore()
const TABS = ['obs', 'history', 'period', 'weather'] // labels: panel.tabs.<id>
const STACK_MAX = 3 // up to this many hexagons, their fichas are stacked; more, a list
const CHIPS_MAX = 8
const tab = ref('obs')
const mode = ref('detail') // 'detail' | 'grouped'
const title = ref(null)
const tabEls = ref([])
const opener = document.activeElement // focus goes back here when the panel closes

function onTabKey(e, i) {
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key]
  if (next === undefined) return
  e.preventDefault()
  const j = (next + TABS.length) % TABS.length
  tab.value = TABS[j]
  tabEls.value[j]?.focus()
}

const onKey = (e) => {
  if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector(':popover-open')) return
  store.clearSelection()
}
onMounted(async () => {
  window.addEventListener('keydown', onKey)
  await nextTick()
  title.value?.focus()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  if (opener?.isConnected) opener.focus()
})
</script>

<template>
  <aside class="hex-panel" :aria-label="$t('panel.label')">
    <header>
      <div class="title">
        <h2 ref="title" tabindex="-1">
          {{ store.selectedHexes.length === 1 ? $t('panel.one') : $t('panel.many', { n: num(store.selectedHexes.length) }) }}
        </h2>
        <button class="close" :aria-label="$t('panel.close')" @click="store.clearSelection()">✕</button>
      </div>
      <ul v-if="store.selectedHexes.length <= CHIPS_MAX" class="chips">
        <li v-for="h in store.selectedHexes" :key="h" class="chip">
          <code>{{ h }}</code>
          <button :aria-label="$t('panel.remove', { id: h })" @click="store.removeHex(h)">✕</button>
        </li>
      </ul>
      <p v-else class="hint">{{ (store.zoneCells.length ? $t('panel.fromArea') : $t('panel.fromMap')) }}</p>
      <p class="hint">{{ $t('panel.hint') }}</p>
      <div class="tabs" role="tablist" :aria-label="$t('panel.tabsLabel')">
        <button
          v-for="(id, i) in TABS" :id="`tab-${id}`" :key="id" ref="tabEls"
          role="tab" :aria-selected="tab === id" :aria-controls="`tabpanel-${id}`" :tabindex="tab === id ? 0 : -1"
          :class="{ on: tab === id }" @click="tab = id" @keydown="onTabKey($event, i)"
        >
          {{ $t(`panel.tabs.${id}`) }}
        </button>
      </div>
    </header>

    <div :id="`tabpanel-${tab}`" class="body" role="tabpanel" :aria-labelledby="`tab-${tab}`" tabindex="0">
      <HexObservations v-if="tab === 'obs'" :hexes="store.selectedHexes" />

      <template v-else>
        <div v-if="store.selectedHexes.length > 1" class="mode" role="group" :aria-label="$t('panel.view')">
          <button :aria-pressed="mode === 'detail'" :class="{ on: mode === 'detail' }" @click="mode = 'detail'">
            {{ $t('panel.detail') }}
          </button>
          <button :aria-pressed="mode === 'grouped'" :class="{ on: mode === 'grouped' }" @click="mode = 'grouped'">
            {{ $t('panel.grouped') }} <small>{{ $t('panel.wip') }}</small>
          </button>
        </div>

        <div v-if="store.selectedHexes.length > 1 && mode === 'grouped'" class="wip">
          <strong>{{ $t('panel.wipTitle') }}</strong>
          <p>{{ $t('panel.wipText') }}</p>
          <p>{{ $t('panel.wipUseDetail') }}</p>
        </div>

        <HexRanking v-else-if="store.selectedHexes.length > STACK_MAX" :key="tab" :hexes="store.selectedHexes" :kind="tab" />

        <template v-else>
          <div v-for="h in store.selectedHexes" :key="`${tab}-${h}`" class="card">
            <HexHistory v-if="tab === 'history'" :h3="h" />
            <HexPeriod v-else-if="tab === 'period'" :h3="h" />
            <HexClimate v-else :h3="h" />
          </div>
        </template>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.hex-panel {
  display: flex; flex-direction: column; min-height: 0; height: 100%;
  background: var(--panel); border-left: 1px solid var(--border);
}
header { padding: 12px 14px 0; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 6px; }
.title { display: flex; justify-content: space-between; align-items: center; }
h2 { margin: 0; font-size: 0.9375rem; }
h2:focus { outline: none; }
h2:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.close { border: none; background: none; font-size: 1rem; cursor: pointer; color: var(--muted); padding: 4px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 0; padding: 0; list-style: none; }
.chip {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 4px 2px 8px; font-size: 0.6875rem;
  border: 1px solid var(--selected); border-radius: 999px; color: var(--selected); background: var(--highlight);
}
.chip button { border: none; background: none; cursor: pointer; color: inherit; font-size: 0.6875rem; padding: 0 2px; }
.hint { margin: 0; font-size: 0.6875rem; color: var(--muted); }
.tabs { display: flex; gap: 2px; margin-top: 4px; }
.tabs button {
  border: none; background: none; font: inherit; font-size: 0.8125rem; padding: 8px 10px; cursor: pointer;
  color: var(--muted); border-bottom: 2px solid transparent;
}
.tabs button.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
.body { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 12px; }
.mode { display: flex; gap: 2px; }
.mode button {
  border: 1px solid var(--border); background: var(--panel); border-radius: 6px; padding: 4px 10px;
  font: inherit; font-size: 0.75rem; color: var(--text); cursor: pointer;
}
.mode button.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
.mode small { font-size: 0.625rem; opacity: 0.8; }
.card { border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.wip { border: 1px dashed var(--border); border-radius: 8px; padding: 12px; font-size: 0.8125rem; background: var(--wip); }
.wip p { margin: 6px 0 0; color: var(--text); }
</style>
