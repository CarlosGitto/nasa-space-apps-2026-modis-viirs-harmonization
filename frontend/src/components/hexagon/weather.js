// Shared by the hexagon's weather views (HexWeather in Ficha · período, HexClimate in the
// Clima tab): summary figures and labels from GET /hexagons/{h3}/weather.
import { mdiThermometer, mdiWaterOff, mdiWaterPercent, mdiWeatherRainy, mdiWeatherSunny, mdiWeatherWindy } from '@mdi/js'
import { num } from './charts'

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
    { label: 'Temp. máx', icon: mdiThermometer, color: '#dc2626', value: withUnit(maxOf(days, 't2m_max_c'), 1, '°C') },
    { label: 'Humedad mín', icon: mdiWaterPercent, color: '#0d9488', value: withUnit(minOf(days, 'rh_min_pct'), 0, '%') },
    { label: 'VPD máx', icon: mdiWeatherSunny, color: '#7c3aed', value: withUnit(maxOf(days, 'vpd_max_kpa'), 1, 'kPa') },
    { label: 'Viento máx', icon: mdiWeatherWindy, color: '#475569', value: withUnit(maxOf(days, 'wind_speed_max_ms'), 1, 'm/s') },
    { label: 'Lluvia total', icon: mdiWeatherRainy, color: '#2563eb', value: rain.length ? `${num(rain.reduce((a, b) => a + b, 0), 1)} mm` : '–' },
    { label: 'Días sin lluvia', icon: mdiWaterOff, color: '#64748b', value: num(last?.days_since_rain), note: last ? `al ${last.date}` : '' },
  ]
}

// "MERRA-2 a 21 km · IMERG a 3 km": which grid cells stand for the hexagon.
export const cellsLabel = (weather) =>
  (weather?.cells ?? [])
    .map((c) => `${c.product === 'merra2' ? 'MERRA-2' : 'IMERG'} a ${num(c.distance_km, 1)} km`)
    .join(' · ')

// The newest days have IMERG but not MERRA-2 yet (published weeks later).
export const merraMissing = (days) => days.some((d) => d.precipitation_mm !== null && d.t2m_max_c === null)

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO']
// Where the wind comes from, as a compass point (0° = north).
export const compass = (deg) => (deg === null || deg === undefined ? '–' : COMPASS[Math.round(deg / 45) % 8])

// The 'YYYY-MM-DD' that is `days` before `iso`.
export const daysBefore = (iso, days) => {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

const PRODUCTS = { merra2: 'MERRA-2 (temperatura, humedad, viento)', imerg: 'IMERG (lluvia)' }

// "MERRA-2 (…) 2025-01-01 → 2025-01-31 · IMERG (lluvia) 2025-01-01 → 2025-03-03": the
// days loaded for this place, per product.
export const coverageLabel = (weather) =>
  (weather?.cells ?? [])
    .filter((c) => c.first_date)
    .map((c) => `${PRODUCTS[c.product]}: ${c.first_date} → ${c.last_date}`)
    .join(' · ')

export const hasTemperature = (days) => days.some((d) => d.t2m_max_c !== null && d.t2m_max_c !== undefined)

// Dates to jump to when the selected ones have no weather: the last `days` days that have
// MERRA-2 (it lags, so they also have rain), else the last ones with any product.
export function suggestedRange(weather, days = 30) {
  const cells = (weather?.cells ?? []).filter((c) => c.last_date)
  const cell = cells.find((c) => c.product === 'merra2') ?? cells[0]
  if (!cell) return null
  const start = daysBefore(cell.last_date, days - 1)
  return { start: start > cell.first_date ? start : cell.first_date, end: cell.last_date }
}
