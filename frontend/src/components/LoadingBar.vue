<script setup>
// Slim loading bar at the top of the app (like Instagram's): a 3 px line that grows
// from the left while something loads, fast at first and slower and slower, never
// reaching the end on its own. It completes (100 %) only when the load is really done
// (for the map: the new hexagons drawn), then fades out. Colored by what is loading
// (stores/loading.js). Decorative: the map and the panels announce loading to screen readers.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useLoadingStore } from '../stores/loading'

const SHOW_AFTER_MS = 150 // quick loads don't flicker
const FADE_MS = 300

const loading = useLoadingStore()
const busy = computed(() => loading.active.length > 0)
const phase = ref('idle') // 'idle' | 'growing' | 'done'
let showTimer = null
let fadeTimer = null

// Colors of the current load, kept while it completes and fades out.
const lastColors = ref(['#f97316'])
watch(() => loading.colors, (c) => { if (c.length) lastColors.value = c })
const background = computed(() => {
  const c = lastColors.value
  return c.length === 1 ? c[0] : `linear-gradient(90deg, ${c.join(', ')})`
})

watch(busy, (on) => {
  clearTimeout(showTimer)
  clearTimeout(fadeTimer)
  if (on) {
    // Restart from zero if it was finishing; wait a moment before showing it.
    if (phase.value !== 'growing') {
      phase.value = 'idle'
      showTimer = setTimeout(() => { if (busy.value) phase.value = 'growing' }, SHOW_AFTER_MS)
    }
  } else if (phase.value === 'growing') {
    phase.value = 'done' // jump to the end, then fade
    fadeTimer = setTimeout(() => { phase.value = 'idle' }, FADE_MS * 2)
  } else {
    phase.value = 'idle' // finished before it was even shown
  }
})

onBeforeUnmount(() => { clearTimeout(showTimer); clearTimeout(fadeTimer) })
</script>

<template>
  <div class="loading-bar" :class="phase" aria-hidden="true">
    <div class="line" :style="{ background }"></div>
  </div>
</template>

<style scoped>
.loading-bar {
  position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 2000; overflow: hidden;
  pointer-events: none;
}
.line {
  height: 100%; width: 100%; border-radius: 0 2px 2px 0;
  transform-origin: left; transform: scaleX(0); opacity: 0;
}
/* Growing: most of the way in ~2 s, then barely moving: it never "finishes" by itself. */
.growing .line { opacity: 1; transform: scaleX(0.9); transition: transform 12s cubic-bezier(0.05, 0.75, 0.1, 1); }
/* Done: fill the rest quickly, then fade. */
.done .line {
  opacity: 0; transform: scaleX(1);
  transition: transform 0.2s ease-out, opacity 0.3s ease 0.25s;
}
/* Reduced motion (accessibility menu or the system): a still, full line while loading. */
:global(html[data-reduce-motion]) .growing .line { transform: scaleX(1); transition: none; }
:global(html[data-reduce-motion]) .done .line { transition: none; }
/* High contrast: a bit thicker. */
:global(html[data-contrast]) .loading-bar { height: 4px; }
</style>
