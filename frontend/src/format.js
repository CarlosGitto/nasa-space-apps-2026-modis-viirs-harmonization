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

// Short number for a calendar cell: 1.2K / 1,2 mil.
export const compact = (value) => (value === null || value === undefined || Number.isNaN(value) ? '–' : g.n(value, 'compact'))

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

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1)
// '2019-08-..' -> 'August 2019' / 'Agosto de 2019'; month (1-12) -> 'August' / 'Agosto'.
export const monthLong = (iso) => cap(g.d(utcDay(iso), 'monthLong'))
export const monthFull = (m) => cap(g.d(new Date(Date.UTC(2000, m - 1, 1)), 'monthOnly'))
// Weekday initials, Monday first (2024-01-01 was a Monday).
export const weekdays = () => Array.from({ length: 7 }, (_, i) => cap(g.d(new Date(Date.UTC(2024, 0, 1 + i)), 'weekdayNarrow')))

const lastOfMonth = (iso) => new Date(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7), 0)).toISOString().slice(0, 10)
// A period as people say it: one day, 'August 2019', '2019', else 'Aug 1, 2019 – Sep 3, 2019'.
export function period(start, end) {
  if (!start || !end) return '–'
  if (start === end) return date(start, 'long')
  if (start.endsWith('-01') && end === lastOfMonth(start)) return monthLong(start)
  if (start.slice(0, 4) === end.slice(0, 4) && start.endsWith('-01-01') && end.endsWith('-12-31')) return start.slice(0, 4)
  return `${date(start)} – ${date(end)}`
}

// Error from api.js -> a message in the user's language (the API speaks English).
export function errorMessage(e) {
  if (!e?.status) return g.t('errors.network')
  return [404, 422, 500, 503].includes(e.status) ? g.t(`errors.${e.status}`) : g.t('errors.other', { status: e.status })
}

// A layer the ETL hasn't built yet (harmonized series, reliability): the API says so in a 404.
export const notBuilt = (e) => e?.status === 404 && /not been built/.test(e.message)
