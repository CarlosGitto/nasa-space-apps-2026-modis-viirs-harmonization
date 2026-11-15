// Weather layers of the main map (GET /weather/map): one UTC day of temperature,
// precipitation or wind drawn over the fire hexagons. Temperature and rain are grid
// cells colored by value; wind is an arrow per cell pointing where the wind blows.
import { EMPTY } from './mapShared'

// Layer id -> how it's shown. stops: [value, color] for the color ramp and its legend.
export const WEATHER_LAYERS = {
  temperature: {
    label: 'Temperature',
    title: 'Max temperature (°C) · NASA MERRA-2',
    stops: [[-10, '#313695'], [0, '#4575b4'], [10, '#abd9e9'], [20, '#fee090'], [30, '#f46d43'], [40, '#a50026']],
  },
  precipitation: {
    label: 'Rain',
    title: 'Rain (mm/day) · NASA GPM IMERG · dry cells hidden',
    stops: [[0.1, '#c6dbef'], [1, '#6baed6'], [5, '#2171b5'], [20, '#08519c'], [50, '#54278f']],
  },
  wind: {
    label: 'Wind',
    title: 'Wind (m/s, daily mean) · NASA MERRA-2 · arrows point where it blows',
    stops: [[0, '#94a3b8'], [2, '#38bdf8'], [5, '#22c55e'], [8, '#eab308'], [12, '#ef4444'], [18, '#7f1d1d']],
  },
}
export const WEATHER_IDS = Object.keys(WEATHER_LAYERS)

// MapLibre color expression over a feature's `value` from a layer's stops.
export const colorRamp = (id) => ['interpolate', ['linear'], ['get', 'value'], ...WEATHER_LAYERS[id].stops.flat()]

// CSS gradient for the legend, stops spread evenly (the ramp is not linear in value).
export const legendGradient = (id) => {
  const stops = WEATHER_LAYERS[id].stops
  return `linear-gradient(to right, ${stops.map(([, c], i) => `${c} ${(i / (stops.length - 1)) * 100}%`).join(', ')})`
}

// GET /weather/map response -> GeoJSON: a rectangle per cell (temperature, rain) or
// a point with the direction the wind blows TO (wind: the API gives where it comes from).
export function weatherFeatures(id, response) {
  if (!response?.cells?.length) return EMPTY
  const [dlat, dlon] = response.cell.map((d) => d / 2)
  const features = response.cells.map(([lon, lat, value, from]) =>
    id === 'wind'
      ? { type: 'Feature', properties: { value, from, to: (from + 180) % 360 }, geometry: { type: 'Point', coordinates: [lon, lat] } }
      : {
          type: 'Feature',
          properties: { value },
          geometry: {
            type: 'Polygon',
            coordinates: [[[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]],
          },
        },
  )
  return { type: 'FeatureCollection', features }
}

// Arrow pointing north, drawn once as an SDF icon so MapLibre can color it per feature.
export function windArrowImage(size = 32) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')
  const c = size / 2
  ctx.fillStyle = '#000'
  ctx.beginPath()
  ctx.moveTo(c, 2) // tip
  ctx.lineTo(c + size * 0.28, size * 0.45)
  ctx.lineTo(c + size * 0.09, size * 0.45)
  ctx.lineTo(c + size * 0.09, size - 3)
  ctx.lineTo(c - size * 0.09, size - 3)
  ctx.lineTo(c - size * 0.09, size * 0.45)
  ctx.lineTo(c - size * 0.28, size * 0.45)
  ctx.closePath()
  ctx.fill()
  return ctx.getImageData(0, 0, size, size)
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const compass = (deg) => COMPASS[Math.round(deg / 45) % 8]

// One tooltip line per weather layer under the cursor.
export function weatherTooltip(features) {
  return features
    .map((f) => {
      const id = f.layer.id.replace('wx-', '')
      const v = Number(f.properties.value)
      if (id === 'temperature') return `🌡 Max temperature: <b>${v.toFixed(1)} °C</b>`
      if (id === 'precipitation') return `🌧 Rain: <b>${v.toFixed(1)} mm</b>`
      return `💨 Wind: <b>${v.toFixed(1)} m/s</b> from ${compass(Number(f.properties.from))}`
    })
    .join('<br>')
}
