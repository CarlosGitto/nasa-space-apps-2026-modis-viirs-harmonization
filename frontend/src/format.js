// Numbers and dates in the user's language (see i18n.js). Reactive: used in a template,
// they update when the language changes.
import { i18n } from './i18n'

const g = i18n.global

// Number with `digits` decimals (0 and 1: up to; 2 and 4: exactly); '–' when missing.
const FORMATS = { 0: 'integer', 1: 'decimal1', 2: 'decimal2', 4: 'decimal4' }
export function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return '–'
  return g.n(value, FORMATS[digits] ?? 'decimal4')
}

// Number with 2 significant digits (0.0034, 1.2, 4.500): for values spanning orders of magnitude.
export const sig = (value) => (value === null || value === undefined || Number.isNaN(value) ? '–' : g.n(value, 'significant2'))

// A dataset as the controls name it: 'VIIRS SP + NRT · continuo'.
const SENSOR_NAMES = { modis: 'MODIS', viirs: 'VIIRS' }
export const datasetName = (d) =>
  d ? `${SENSOR_NAMES[d.sensor] ?? g.t(`controls.sensors.${d.sensor}`)} ${g.t(`controls.products.${d.product}`)}` : ''

// Share (0..1) as a percentage; '–' when missing.
export const pct = (share) => (share === null || share === undefined ? '–' : g.n(share, 'percent'))

const utcDay = (iso) => new Date(`${iso.slice(0, 10)}T00:00:00Z`)

// 'YYYY-MM-DD' (or a timestamp) as a calendar day: 'Nov 12, 2026' / '12 nov 2026'.
export const date = (iso, style = 'short') => (iso ? g.d(utcDay(iso), style) : '–')

// Month name (1-12), capitalized: 'Jan' / 'Ene'.
export function monthName(m) {
  const s = g.d(new Date(Date.UTC(2000, m - 1, 1)), 'monthShort').replace('.', '')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// Error from api.js -> a message in the user's language (the API speaks English).
export function errorMessage(e) {
  if (!e?.status) return g.t('errors.network')
  return [404, 422, 500, 503].includes(e.status) ? g.t(`errors.${e.status}`) : g.t('errors.other', { status: e.status })
}

// A layer the ETL hasn't built yet (harmonized series, reliability): the API says so in a 404.
export const notBuilt = (e) => e?.status === 404 && /not been built/.test(e.message)
