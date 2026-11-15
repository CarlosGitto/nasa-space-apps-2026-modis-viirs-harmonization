// Chart options and number formats shared by the hexagon fichas.
// One bar series per sensor, side by side: MODIS and VIIRS are never summed.
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'

export const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

export const num = (n, digits = 0) =>
  n === null || n === undefined ? '–' : n.toLocaleString('es-AR', { maximumFractionDigits: digits })
export const pct = (share) => (share === null || share === undefined ? '–' : `${Math.round(share * 100)} %`)

const TEXT = '#1f2937'
const MUTED = '#6b7280'
const GRID = '#e5e7eb'

// categories: x labels; bySensor: { modis: [n per category], viirs: [...] }
export function sensorBars(categories, bySensor, { labelInterval = 'auto', decimals = 0, format = null, max = null } = {}) {
  const sensors = Object.keys(bySensor)
  return {
    animation: false,
    grid: { left: 40, right: 8, top: sensors.length > 1 ? 28 : 10, bottom: 22 },
    legend: sensors.length > 1
      ? { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: TEXT, fontSize: 11 } }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: format ?? ((v) => `${num(v, decimals)} detecciones`),
    },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { color: MUTED, fontSize: 10, interval: labelInterval, hideOverlap: true },
      axisLine: { lineStyle: { color: GRID } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: decimals || max ? 0 : 1,
      ...(max ? { min: 0, max } : {}),
      axisLabel: { color: MUTED, fontSize: 10 },
      splitLine: { lineStyle: { color: GRID } },
    },
    series: sensors.map((sensor) => ({
      name: SENSOR_LABELS[sensor] ?? sensor,
      type: 'bar',
      data: bySensor[sensor],
      itemStyle: { color: SENSOR_COLORS[sensor], borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 14,
      barGap: '15%',
      emphasis: { focus: 'series' },
    })),
  }
}

// 'YYYY-MM-01' strings from the first to the last month (inclusive).
export function monthRange(first, last) {
  const out = []
  let [y, m] = first.split('-').map(Number)
  const [ly, lm] = last.split('-').map(Number)
  while (y < ly || (y === ly && m <= lm)) {
    out.push(`${y}-${String(m).padStart(2, '0')}-01`)
    m += 1
    if (m > 12) { m = 1; y += 1 }
  }
  return out
}

// 'YYYY-MM-DD' strings from start to end (inclusive).
export function dayRange(start, end) {
  const out = []
  for (let d = new Date(`${start}T00:00:00Z`); d <= new Date(`${end}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    out.push(d.toISOString().slice(0, 10))
  }
  return out
}

export const monthLabel = (iso) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(2, 4)}`

// Series spliced across the MODIS -> VIIRS change (fct_hexagon_harmonized_monthly), in MODIS units.
// data: { months, covered_months } from the API. A month without a row is a zero only where the data was downloaded
// (covered_months); elsewhere it is a gap (null), never a zero. The monthly value is noisy (a cell has few
// detections): the 12-month mean is drawn on top, over covered months only.
export const SPLICE_MONTH = '2012-02-01'
export function harmonizedLine(data, metric) {
  const rows = data?.months ?? []
  if (!rows.length) return null
  const covered = new Set(data.covered_months)
  const all = [...rows.map((r) => r.month), ...data.covered_months].sort()
  const categories = monthRange(all[0], all.at(-1))
  const key = metric === 'frp' ? 'frp_harmonized' : 'detections_harmonized'
  const byMonth = new Map(rows.map((r) => [r.month, r[key]]))
  const monthly = categories.map((m) => (byMonth.has(m) ? byMonth.get(m) : covered.has(m) ? 0 : null))
  const trend = monthly.map((_, i) => {
    if (i < 11) return null
    const w = monthly.slice(i - 11, i + 1)
    return w.some((v) => v === null) ? null : w.reduce((a, b) => a + b, 0) / 12
  })
  const unit = metric === 'frp' ? 'MW' : 'detecciones'
  const spliceAt = categories.indexOf(SPLICE_MONTH)
  return {
    animation: false,
    grid: { left: 44, right: 8, top: 28, bottom: 22 },
    legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 8, textStyle: { color: TEXT, fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v === null || v === undefined ? '–' : `${num(v, 1)} ${unit}`),
    },
    xAxis: {
      type: 'category',
      data: categories.map(monthLabel),
      axisLabel: { color: MUTED, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: GRID } },
      axisTick: { show: false },
    },
    yAxis: { type: 'value', axisLabel: { color: MUTED, fontSize: 10 }, splitLine: { lineStyle: { color: GRID } } },
    series: [
      {
        name: 'Mensual', type: 'line', data: monthly, showSymbol: false,
        lineStyle: { width: 1, color: '#9ca3af' }, itemStyle: { color: '#9ca3af' },
        markLine: spliceAt < 0 ? undefined : {
          symbol: 'none', silent: true,
          lineStyle: { color: MUTED, type: 'dashed' },
          label: { formatter: 'MODIS → VIIRS', color: MUTED, fontSize: 10 },
          data: [{ xAxis: spliceAt }],
        },
      },
      {
        name: 'Media 12 meses', type: 'line', data: trend, showSymbol: false, smooth: true,
        lineStyle: { width: 2, color: '#eb6834' }, itemStyle: { color: '#eb6834' },
      },
    ],
  }
}

// Reliability of the detections by year and sensor (fct_hexagon_reliability_monthly): months are weighted by their
// detections, so a month with two detections does not count as much as one with two hundred.
// metric: 'reliability' (mean probability that an independent sensor confirms) | 'persistent' (share of persistent detections)
export function reliabilityByYear(rows, metric) {
  if (!rows.length) return null
  const years = [...new Set(rows.map((r) => Number(r.month.slice(0, 4))))].sort((a, b) => a - b)
  const span = Array.from({ length: years.at(-1) - years[0] + 1 }, (_, i) => years[0] + i)
  const acc = {}
  for (const r of rows) {
    const a = ((acc[r.sensor] ??= {})[r.month.slice(0, 4)] ??= { det: 0, rel: 0, per: 0 })
    a.det += r.detections
    a.rel += r.reliability_sum
    a.per += r.persistent_detections
  }
  const bySensor = {}
  for (const [sensor, byYear] of Object.entries(acc)) {
    bySensor[sensor] = span.map((y) => {
      const a = byYear[y]
      return a ? (metric === 'reliability' ? a.rel / a.det : a.per / a.det) : null
    })
  }
  const label = metric === 'reliability' ? 'índice medio' : 'detecciones persistentes'
  return sensorBars(span.map(String), bySensor, {
    labelInterval: 0, max: 1,
    format: (v) => (v === null || v === undefined ? 'sin detecciones' : metric === 'reliability' ? `${num(v, 2)} · ${label}` : `${pct(v)} ${label}`),
  })
}

// Overall figures of one sensor from the monthly rows, weighted by detections.
export function reliabilityTotals(rows, sensor) {
  const mine = rows.filter((r) => r.sensor === sensor)
  const det = mine.reduce((s, r) => s + r.detections, 0)
  if (!det) return null
  return {
    detections: det,
    reliability: mine.reduce((s, r) => s + r.reliability_sum, 0) / det,
    persistent: mine.reduce((s, r) => s + r.persistent_detections, 0) / det,
  }
}
