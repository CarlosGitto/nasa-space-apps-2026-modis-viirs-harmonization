// The user's preferences: language and accessibility options (the menu with the
// accessibility icon). Saved in this browser and applied as attributes on <html>,
// which the styles (style.css) and the maps read:
//   lang, data-text-size (normal|large|xlarge), data-contrast, data-reduce-motion, data-easy-reading
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { i18n, LOCALES } from '../i18n'
import { readPrefs, savePrefs } from '../prefsStorage'

const TEXT_SIZES = ['normal', 'large', 'xlarge']
const systemReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

export const usePrefsStore = defineStore('prefs', () => {
  const saved = readPrefs()
  const locale = ref(i18n.global.locale.value)
  const textSize = ref(TEXT_SIZES.includes(saved.textSize) ? saved.textSize : 'normal')
  const contrast = ref(saved.contrast === true)
  const reduceMotion = ref(typeof saved.reduceMotion === 'boolean' ? saved.reduceMotion : systemReducedMotion())
  const easyReading = ref(saved.easyReading === true)

  function apply() {
    const html = document.documentElement
    i18n.global.locale.value = locale.value
    html.lang = locale.value
    document.title = i18n.global.t('app.title') // the browser tab too
    html.dataset.textSize = textSize.value
    html.toggleAttribute('data-contrast', contrast.value)
    html.toggleAttribute('data-reduce-motion', reduceMotion.value)
    html.toggleAttribute('data-easy-reading', easyReading.value)
    if (easyReading.value) { // downloaded only when used
      import('@fontsource/atkinson-hyperlegible/400.css')
      import('@fontsource/atkinson-hyperlegible/700.css')
    }
    savePrefs({
      locale: locale.value, textSize: textSize.value, contrast: contrast.value,
      reduceMotion: reduceMotion.value, easyReading: easyReading.value,
    })
  }
  watch([locale, textSize, contrast, reduceMotion, easyReading], apply, { immediate: true })

  const setLocale = (l) => { if (LOCALES.includes(l)) locale.value = l }
  function reset() {
    textSize.value = 'normal'
    contrast.value = false
    reduceMotion.value = systemReducedMotion()
    easyReading.value = false
  }

  return { locale, textSize, contrast, reduceMotion, easyReading, TEXT_SIZES, setLocale, reset }
})
