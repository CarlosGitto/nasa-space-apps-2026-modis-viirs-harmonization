// Shared by the hexagon's weather views (HexWeather in Ficha · período, HexClimate in the
// Clima tab): summary figures and labels from GET /hexagons/{h3}/weather.
import { mdiThermometer, mdiWaterOff, mdiWaterPercent, mdiWeatherRainy, mdiWeatherSunny, mdiWeatherWindy } from '@mdi/js'
import { date, num } from '../../format'
import { i18n } from '../../i18n'
import { compass } from '../../mapWeather'

export { compass }
const t = (key, values) => i18n.global.t(key, values)

const present = (days, key) => days.map((d) => d[key]).filter((v) => v !== null && v !== undefined)
const maxOf = (days, key) => (present(days, key).length ? Math.max(...present(days, key)) : null)
const minOf = (days, key) => (present(days, key).length ? Math.min(...present(days, key)) : null)

// Headline figures of a range of days (the worst values for fire: hottest, driest, windiest).
export function weatherKpis(days) {
  const rain = present(days, 'precipitation_mm')
  const last = days.at(-1)
  // '–' alone when there is no value, never '– °C'.
  const withUnit = (v, digits, unit) => (v === null ? '–' : `${num(v, digits)} ${unit}`)
  // icon: Material Design Icons path (the map uses the same family); color: the variable's.
  return [
    { label: t('weather.kpi.tmax'), icon: mdiThermometer, color: '#dc2626', value: withUnit(maxOf(days, 't2m_max_c'), 1, '°C') },
    { label: t('weather.kpi.rhMin'), icon: mdiWaterPercent, color: '#0d9488', value: withUnit(minOf(days, 'rh_min_pct'), 0, '%') },
    { label: t('weather.vpdMax'), icon: mdiWeatherSunny, color: '#7c3aed', value: withUnit(maxOf(days, 'vpd_max_kpa'), 1, 'kPa') },
    { label: t('weather.kpi.windMax'), icon: mdiWeatherWindy, color: '#475569', value: withUnit(maxOf(days, 'wind_speed_max_ms'), 1, 'm/s') },
    { label: t('weather.kpi.rainTotal'), icon: mdiWeatherRainy, color: '#2563eb', value: rain.length ? `${num(rain.reduce((a, b) => a + b, 0), 1)} mm` : '–' },
    { label: t('weather.kpi.dryDays'), icon: mdiWaterOff, color: '#64748b', value: num(last?.days_since_rain), note: last ? t('ficha.asOf', { date: date(last.date) }) : '' },
  ]
}

// "NASA POWER cell 16 km away": which grid cell stands for the hexagon.
export const cellsLabel = (weather) =>
  (weather?.cells ?? []).map((c) => t('weather.cell', { km: num(c.distance_km, 1) })).join(' · ')

// The newest days may have rain but not temperature yet (POWER fills them in ~2 days).
export const merraMissing = (days) => days.some((d) => d.precipitation_mm !== null && d.t2m_max_c === null)


// The 'YYYY-MM-DD' that is `days` before `iso`.
export const daysBefore = (iso, days) => {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

const PRODUCTS = { power: 'POWER' }

// "NASA POWER: 1981-01-01 → 2026-11-13": the days available for this place.
export const coverageLabel = (weather) =>
  (weather?.cells ?? [])
    .filter((c) => c.first_date)
    .map((c) => `${PRODUCTS[c.product]}: ${date(c.first_date)} → ${date(c.last_date)}`)
    .join(' · ')

export const hasTemperature = (days) => days.some((d) => d.t2m_max_c !== null && d.t2m_max_c !== undefined)

// Dates to jump to when the selected ones have no weather: the last `days` days that have
// data.
export function suggestedRange(weather, days = 30) {
  const cells = (weather?.cells ?? []).filter((c) => c.last_date)
  const cell = cells[0]
  if (!cell) return null
  const start = daysBefore(cell.last_date, days - 1)
  return { start: start > cell.first_date ? start : cell.first_date, end: cell.last_date }
}
