<script setup>
// ES | EN: the language of the UI (data and technical names stay as they are).
import { usePrefsStore } from '../stores/prefs'
import { LOCALES } from '../i18n'

const prefs = usePrefsStore()
// Each language's name in itself, so anyone can find theirs.
const NAMES = { es: 'Español', en: 'English' }
</script>

<template>
  <div class="lang" role="group" :aria-label="$t('app.language')">
    <button
      v-for="l in LOCALES" :key="l" :lang="l" :aria-pressed="prefs.locale === l"
      :class="{ on: prefs.locale === l }" @click="prefs.setLocale(l)"
    >
      {{ l.toUpperCase() }}<span class="sr-only"> · {{ NAMES[l] }}</span>
    </button>
  </div>
</template>

<style scoped>
.lang { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
button {
  border: none; background: var(--panel); color: var(--muted); padding: 4px 7px; cursor: pointer;
  font: inherit; font-size: 0.75rem; font-weight: 600;
}
button.on { background: var(--accent); color: var(--on-accent); }
</style>
