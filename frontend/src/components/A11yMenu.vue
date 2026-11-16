<script setup>
// Accessibility menu (the universal-access icon): text size, high contrast, reduced
// motion and easy reading. Native HTML popover: Esc and clicking outside close it, and
// the button announces whether it's open. Choices are saved in this browser (prefs store).
import { usePrefsStore } from '../stores/prefs'

const prefs = usePrefsStore()
</script>

<template>
  <button class="trigger" popovertarget="a11y-menu" :aria-label="$t('a11y.button')" :title="$t('a11y.button')">
    <!-- Universal access symbol -->
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" stroke-width="1.6" />
      <circle cx="12" cy="6.3" r="1.7" fill="currentColor" />
      <path
        d="M6.5 9.2l5.5 1.2 5.5-1.2M12 10.4v4.1m0 0l-2.6 5m2.6-5l2.6 5"
        fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
      />
    </svg>
  </button>

  <div id="a11y-menu" popover class="menu" role="dialog" aria-labelledby="a11y-title">
    <h2 id="a11y-title">{{ $t('a11y.title') }}</h2>

    <fieldset>
      <legend>{{ $t('a11y.textSize') }}</legend>
      <label v-for="size in prefs.TEXT_SIZES" :key="size" class="option">
        <input v-model="prefs.textSize" type="radio" name="text-size" :value="size" />
        {{ $t(`a11y.${size}`) }}
      </label>
    </fieldset>

    <label class="option">
      <input v-model="prefs.contrast" type="checkbox" aria-describedby="a11y-contrast-hint" />
      <span>
        {{ $t('a11y.contrast') }}
        <small id="a11y-contrast-hint">{{ $t('a11y.contrastHint') }}</small>
      </span>
    </label>
    <label class="option">
      <input v-model="prefs.reduceMotion" type="checkbox" aria-describedby="a11y-motion-hint" />
      <span>
        {{ $t('a11y.reduceMotion') }}
        <small id="a11y-motion-hint">{{ $t('a11y.reduceMotionHint') }}</small>
      </span>
    </label>
    <label class="option">
      <input v-model="prefs.easyReading" type="checkbox" aria-describedby="a11y-reading-hint" />
      <span>
        {{ $t('a11y.easyReading') }}
        <small id="a11y-reading-hint">{{ $t('a11y.easyReadingHint') }}</small>
      </span>
    </label>

    <div class="actions">
      <button @click="prefs.reset()">{{ $t('a11y.reset') }}</button>
      <button popovertarget="a11y-menu" popovertargetaction="hide">{{ $t('a11y.close') }}</button>
    </div>
  </div>
</template>

<style scoped>
.trigger {
  display: grid; place-items: center; width: 2rem; height: 2rem; padding: 0; border-radius: 50%;
  border: 1px solid var(--border); background: var(--panel); color: var(--accent); cursor: pointer;
}
.trigger:hover { background: var(--highlight); }
/* Opens under the button, at the top of the left panel. */
.menu {
  position: fixed; inset: auto; top: 3.75rem; left: 1rem; margin: 0; width: min(20rem, calc(100vw - 2rem));
  padding: 14px 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--panel);
  color: var(--text); box-shadow: 0 8px 24px rgb(0 0 0 / 0.18);
}
h2 { margin: 0 0 10px; font-size: 1rem; }
fieldset { margin: 0 0 10px; padding: 0; border: none; display: flex; flex-direction: column; gap: 4px; }
legend { padding: 0; margin-bottom: 4px; font-size: 0.8125rem; font-weight: 600; }
.option { display: flex; gap: 8px; align-items: flex-start; font-size: 0.875rem; padding: 4px 0; cursor: pointer; }
.option input { margin-top: 0.2rem; accent-color: var(--accent); }
.option small { display: block; color: var(--muted); font-size: 0.75rem; }
.actions { display: flex; justify-content: space-between; margin-top: 10px; gap: 8px; }
.actions button {
  font: inherit; font-size: 0.8125rem; padding: 5px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--panel); color: var(--text);
}
</style>
