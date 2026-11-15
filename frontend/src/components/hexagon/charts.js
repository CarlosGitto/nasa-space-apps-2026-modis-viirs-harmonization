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
export function sensorBars(categories, bySensor, { labelInterval = 'auto' } = {}) {
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
      valueFormatter: (v) => `${num(v)} detecciones`,
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
      minInterval: 1,
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
