// Thin wrappers over the backend (see backend/README.md).
// Every stats/detections call takes the same filters: start, end, bbox, zone.

const BASE = import.meta.env.VITE_API_URL ?? '/api'

async function get(path, params = {}, signal) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    query.set(key, typeof value === 'object' ? JSON.stringify(value) : String(value))
  }
  const qs = query.toString()
  const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`, { signal })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}

async function post(path, body, signal) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return res.json()
}

export const listDatasets = () => get('/datasets')

export const getDetections = (dataset, params, signal) =>
  get(`/datasets/${dataset}/detections`, params, signal)

export const getHexagons = (dataset, params, signal) =>
  get(`/datasets/${dataset}/stats/hex`, params, signal)

export const getMonthly = (dataset, params, signal) =>
  get(`/datasets/${dataset}/stats/monthly`, params, signal)

export const getDaily = (dataset, params, signal) =>
  get(`/datasets/${dataset}/stats/daily`, params, signal)

// One hexagon (fixed H3 res-6 cell). Identity + whole-history profile and the monthly
// series only change when the ETL runs: cache them for the session.
const cache = new Map()
const cached = (key, load) => {
  if (!cache.has(key)) cache.set(key, load().catch((e) => { cache.delete(key); throw e }))
  return cache.get(key)
}

export const getHexagon = (h3) => cached(`hex:${h3}`, () => get(`/hexagons/${h3}`))

export const getHexagonMonthly = (h3) => cached(`monthly:${h3}`, () => get(`/hexagons/${h3}/monthly`))

export const getHexagonPeriod = (h3, params, signal) => get(`/hexagons/${h3}/period`, params, signal)

export const getHexagonDetections = (h3, params, signal) => get(`/hexagons/${h3}/detections`, params, signal)

// Daily weather of a hexagon (NASA MERRA-2 + GPM IMERG, from its nearest grid cells).
export const getHexagonWeather = (h3, params, signal) => get(`/hexagons/${h3}/weather`, params, signal)

// Many hexagons at once (a drawn area or a multi-selection): POST, ids in the body.
export const getSelectionSummary = (body, signal) => post('/hexagons/selection/summary', body, signal)

export const getSelectionDetections = (body, signal) => post('/hexagons/selection/detections', body, signal)
