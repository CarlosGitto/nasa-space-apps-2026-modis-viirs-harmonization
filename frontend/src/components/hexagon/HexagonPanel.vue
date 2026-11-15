<script setup>
// Right-side panel for the selected res-6 hexagons: observations table and the two fichas.
// Several hexagons: "Detalle" stacks one ficha per hexagon (a few) or lists them busiest
// first with their ficha on click (a drawn area); "Agrupado" is WIP (how to add
// hexagons up is not defined yet).
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useFiltersStore } from '../../stores/filters'
import HexHistory from './HexHistory.vue'
import HexObservations from './HexObservations.vue'
import HexPeriod from './HexPeriod.vue'
import HexRanking from './HexRanking.vue'

const store = useFiltersStore()
const fmt = (n) => n.toLocaleString('es-AR')
const TABS = [
  { id: 'obs', label: 'Observaciones' },
  { id: 'history', label: 'Ficha · historial' },
  { id: 'period', label: 'Ficha · período' },
]
const STACK_MAX = 3 // up to this many hexagons, their fichas are stacked; more, a list
const CHIPS_MAX = 8
const tab = ref('obs')
const mode = ref('detail') // 'detail' | 'grouped'

const onKey = (e) => { if (e.key === 'Escape') store.clearSelection() }
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <aside class="hex-panel" aria-label="Hexágonos seleccionados">
    <header>
      <div class="title">
        <h2>{{ store.selectedHexes.length === 1 ? 'Hexágono' : `${fmt(store.selectedHexes.length)} hexágonos` }}</h2>
        <button class="close" aria-label="Cerrar panel" @click="store.clearSelection()">✕</button>
      </div>
      <div v-if="store.selectedHexes.length <= CHIPS_MAX" class="chips">
        <span v-for="h in store.selectedHexes" :key="h" class="chip">
          <code>{{ h }}</code>
          <button :aria-label="`Quitar ${h}`" @click="store.removeHex(h)">✕</button>
        </span>
      </div>
      <p v-else class="hint">
        {{ store.zoneCells.length ? 'Hexágonos del área dibujada, con y sin detecciones.' : 'Hexágonos elegidos en el mapa.' }}
      </p>
      <p class="hint">Shift + click en el mapa suma o quita hexágonos · Esc cierra</p>
      <nav class="tabs" role="tablist">
        <button v-for="t in TABS" :key="t.id" role="tab" :aria-selected="tab === t.id" :class="{ on: tab === t.id }" @click="tab = t.id">
          {{ t.label }}
        </button>
      </nav>
    </header>

    <div class="body">
      <HexObservations v-if="tab === 'obs'" :hexes="store.selectedHexes" />

      <template v-else>
        <div v-if="store.selectedHexes.length > 1" class="mode" role="group" aria-label="Vista">
          <button :class="{ on: mode === 'detail' }" @click="mode = 'detail'">Detalle</button>
          <button :class="{ on: mode === 'grouped' }" @click="mode = 'grouped'">Agrupado <small>WIP</small></button>
        </div>

        <div v-if="store.selectedHexes.length > 1 && mode === 'grouped'" class="wip">
          <strong>Vista agrupada · en construcción</strong>
          <p>
            Todavía no definimos cómo sumar hexágonos. Las detecciones se pueden sumar, pero
            "días con fuego", picos, percentiles de FRP o la actividad reciente no: hay que decidir si
            se calculan sobre la unión de los hexágonos o se resumen las fichas.
          </p>
          <p>Mientras tanto, usá "Detalle": una ficha por hexágono.</p>
        </div>

        <HexRanking v-else-if="store.selectedHexes.length > STACK_MAX" :key="tab" :hexes="store.selectedHexes" :kind="tab" />

        <template v-else>
          <div v-for="h in store.selectedHexes" :key="`${tab}-${h}`" class="card">
            <HexHistory v-if="tab === 'history'" :h3="h" />
            <HexPeriod v-else :h3="h" />
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
h2 { margin: 0; font-size: 15px; }
.close { border: none; background: none; font-size: 16px; cursor: pointer; color: var(--muted); padding: 4px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 4px 2px 8px; font-size: 11px;
  border: 1px solid #1d4ed8; border-radius: 999px; color: #1d4ed8; background: #eff6ff;
}
.chip button { border: none; background: none; cursor: pointer; color: inherit; font-size: 11px; padding: 0 2px; }
.hint { margin: 0; font-size: 11px; color: var(--muted); }
.tabs { display: flex; gap: 2px; margin-top: 4px; }
.tabs button {
  border: none; background: none; font: inherit; font-size: 13px; padding: 8px 10px; cursor: pointer;
  color: var(--muted); border-bottom: 2px solid transparent;
}
.tabs button.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
.body { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 12px; }
.mode { display: flex; gap: 2px; }
.mode button {
  border: 1px solid var(--border); background: var(--panel); border-radius: 6px; padding: 4px 10px;
  font: inherit; font-size: 12px; cursor: pointer;
}
.mode button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.mode small { font-size: 10px; opacity: 0.8; }
.card { border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.wip { border: 1px dashed var(--border); border-radius: 8px; padding: 12px; font-size: 13px; background: #fffbeb; }
.wip p { margin: 6px 0 0; color: var(--text); }
</style>
