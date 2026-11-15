// Numbers and dates in the user's language (see i18n.js). Reactive: used in a template,
// they update when the language changes.
import { i18n } from './i18n'

const g = i18n.global

// Number with up to `digits` decimals; '–' when missing.
export function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return '–'
  return g.n(value, digits === 0 ? 'integer' : digits === 1 ? 'decimal1' : 'decimal4')
}

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
