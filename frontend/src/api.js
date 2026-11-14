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

export const listDatasets = () => get('/datasets')

export const getDetections = (dataset, params, signal) =>
  get(`/datasets/${dataset}/detections`, params, signal)

export const getHexagons = (dataset, params, signal) =>
  get(`/datasets/${dataset}/stats/hex`, params, signal)

export const getMonthly = (dataset, params, signal) =>
  get(`/datasets/${dataset}/stats/monthly`, params, signal)

export const getDaily = (dataset, params, signal) =>
  get(`/datasets/${dataset}/stats/daily`, params, signal)
