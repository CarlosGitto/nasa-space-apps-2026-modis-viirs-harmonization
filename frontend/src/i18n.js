// Translations of the UI (src/locales/*.json): English and Spanish. Only what the user
// reads is translated; code, API, data and technical names (SP, NRT, MODIS, NOAA-20,
// H3 ids) stay as they are. Usage: $t('key') in templates, useI18n().t in components,
// i18n.global.t elsewhere, and the helpers in format.js for numbers and dates.
import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import es from './locales/es.json'
import { readPrefs } from './prefsStorage'

export const LOCALES = ['es', 'en']

// Saved choice, else the browser's language, else English.
function initialLocale() {
  const saved = readPrefs().locale
  if (LOCALES.includes(saved)) return saved
  const browser = (navigator.languages ?? [navigator.language]).find(Boolean) ?? 'en'
  return browser.toLowerCase().startsWith('es') ? 'es' : 'en'
}

// Same formats in both languages; Intl applies each language's conventions
// (1,004.5 vs 1.004,5). Grouping always, so Spanish shows 1.004 too.
const numberFormats = {
  integer: { maximumFractionDigits: 0, useGrouping: 'always' },
  decimal1: { maximumFractionDigits: 1, useGrouping: 'always' },
  decimal4: { minimumFractionDigits: 4, maximumFractionDigits: 4 },
  percent: { style: 'percent', maximumFractionDigits: 0 },
}
// Dates in the data are UTC calendar days: format them in UTC so they never shift.
const datetimeFormats = {
  short: { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' },
  long: { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' },
  monthShort: { month: 'short', timeZone: 'UTC' },
  monthYear: { month: 'short', year: '2-digit', timeZone: 'UTC' },
  dayMonth: { month: 'short', day: 'numeric', timeZone: 'UTC' },
}

export const i18n = createI18n({
  legacy: false,
  locale: initialLocale(),
  fallbackLocale: 'en',
  messages: { en, es },
  numberFormats: { en: numberFormats, es: numberFormats },
  datetimeFormats: { en: datetimeFormats, es: datetimeFormats },
})
