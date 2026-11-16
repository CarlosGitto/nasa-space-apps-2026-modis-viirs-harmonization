// What is loading right now, by kind, for the slim loading bar at the top (LoadingBar.vue).
// Each kind has its own color, taken from what it loads: fire orange for the hexagons,
// olive for the contour lines, the selection blue for the hexagon panel...
import { defineStore } from 'pinia'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

// In order of importance: when several load at once the bar blends their colors in this order.
export const LOADING_KINDS = {
  hexagons: ['#f97316'], // fire data on the map
  panel: ['#2563eb'], // the selected hexagons' observations and fichas
  curtain: ['#ffcc00', '#22d3ee'], // MODIS | VIIRS
  weather: ['#0d9488'], // weather layers and the hexagon's weather (POWER)
  contours: ['#5f7a45'], // contour lines
  imagery: ['#0ea5e9'], // satellite imagery (GIBS)
  stats: ['#fbbf24'], // burn calendar and time slider bars
  basemap: ['#9ca3af'], // OpenFreeMap tiles
}

export const useLoadingStore = defineStore('loading', () => {
  const counts = ref({}) // kind -> loads in progress

  const start = (kind) => { counts.value = { ...counts.value, [kind]: (counts.value[kind] ?? 0) + 1 } }
  const done = (kind) => { counts.value = { ...counts.value, [kind]: Math.max(0, (counts.value[kind] ?? 0) - 1) } }

  const active = computed(() => Object.keys(LOADING_KINDS).filter((k) => counts.value[k] > 0))
  const colors = computed(() => active.value.flatMap((k) => LOADING_KINDS[k]))

  return { counts, active, colors, start, done }
})

// Mirror a component's boolean "loading" ref into the bar (and let go when it unmounts).
export function useLoadingBar(kind, flag) {
  const store = useLoadingStore()
  let on = false
  const set = (value) => {
    if (value && !on) store.start(kind)
    if (!value && on) store.done(kind)
    on = Boolean(value)
  }
  watch(flag, set, { immediate: true })
  onBeforeUnmount(() => set(false))
}

// Map sources (tiles) -> loading kinds, from the first tile requested until the map is
// idle: every tile loaded *and drawn*, label fade-ins finished. ("Source loaded" alone
// comes a frame or more before the lines show, and can flip between batches of tiles.)
export function trackMapSources(map, sourceKinds) {
  const store = useLoadingStore()
  const pending = new Set()
  map.on('sourcedataloading', (e) => {
    const kind = sourceKinds[e.sourceId]
    if (!kind || pending.has(e.sourceId)) return
    pending.add(e.sourceId)
    store.start(kind)
  })
  map.on('idle', () => {
    for (const id of pending) store.done(sourceKinds[id])
    pending.clear()
  })
  return () => { for (const id of pending) store.done(sourceKinds[id]); pending.clear() } // on unmount
}
