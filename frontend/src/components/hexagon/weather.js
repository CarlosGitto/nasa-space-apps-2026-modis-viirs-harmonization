// Shared by the hexagon's weather views (HexWeather in Ficha · período, HexClimate in the
// Clima tab): summary figures and labels from GET /hexagons/{h3}/weather.
import { num } from './charts'

const present = (days, key) => days.map((d) => d[key]).filter((v) => v !== null && v !== undefined)
const maxOf = (days, key) => (present(days, key).length ? Math.max(...present(days, key)) : null)
const minOf = (days, key) => (present(days, key).length ? Math.min(...present(days, key)) : null)

// Headline figures of a range of days (the worst values for fire: hottest, driest, windiest).
export function weatherKpis(days) {
  const rain = present(days, 'precipitation_mm')
  const last = days.at(-1)
  return [
    { label: 'Temp. máx', value: `${num(maxOf(days, 't2m_max_c'), 1)} °C` },
    { label: 'Humedad mín', value: `${num(minOf(days, 'rh_min_pct'))} %` },
    { label: 'VPD máx', value: `${num(maxOf(days, 'vpd_max_kpa'), 1)} kPa` },
    { label: 'Viento máx', value: `${num(maxOf(days, 'wind_speed_max_ms'), 1)} m/s` },
    { label: 'Lluvia total', value: rain.length ? `${num(rain.reduce((a, b) => a + b, 0), 1)} mm` : '–' },
    { label: 'Días sin lluvia', value: num(last?.days_since_rain), note: last ? `al ${last.date}` : '' },
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
