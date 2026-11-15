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

// Daily chart with up to two value axes: bars and lines over the same days.
// series: [{ name, type: 'bar' | 'line', data, axis: 0 | 1, color, unit, digits, stack }]
// axes: ['mm', 'kPa'] (names of the left and right axis); mark: a 'YYYY-MM-DD' to highlight.
export function dailyChart(dates, series, { axes = [''], mark = null, tooltipExtra = null } = {}) {
  const categories = dates.map((d) => d.slice(5))
  const axis = (name, i) => ({
    type: 'value', name, nameTextStyle: { color: MUTED, fontSize: 10 }, scale: name === '°C',
    axisLabel: { color: MUTED, fontSize: 10 }, splitLine: { show: i === 0, lineStyle: { color: GRID } },
  })
  return {
    animation: false,
    grid: { left: 40, right: axes.length > 1 ? 40 : 10, top: 30, bottom: 22 },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: TEXT, fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      formatter: (items) => {
        const day = dates[items[0].dataIndex]
        const lines = items.map((it) => {
          const s = series[it.seriesIndex]
          const v = it.value === null || it.value === undefined ? '–' : `${num(it.value, s.digits ?? 1)} ${s.unit ?? ''}`
          return `${it.marker}${s.name}: <b>${v}</b>`
        })
        const extra = tooltipExtra?.(items[0].dataIndex)
        return [day, ...lines, ...(extra ? [extra] : [])].join('<br>')
      },
    },
    xAxis: {
      type: 'category', data: categories,
      axisLabel: { color: MUTED, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: GRID } }, axisTick: { show: false },
    },
    yAxis: axes.map(axis),
    series: series.map((s, i) => ({
      name: s.name, type: s.type, data: s.data, yAxisIndex: s.axis ?? 0, stack: s.stack,
      itemStyle: { color: s.color, borderRadius: s.type === 'bar' ? [3, 3, 0, 0] : 0 },
      ...(s.type === 'bar' ? { barMaxWidth: 10 } : { showSymbol: false, connectNulls: false, lineStyle: { color: s.color, width: 2 } }),
      // The time slider's day, once per chart.
      ...(i === 0 && mark && dates.includes(mark)
        ? { markLine: { symbol: 'none', silent: true, label: { show: false }, lineStyle: { color: MUTED, type: 'dashed' },
                        data: [{ xAxis: mark.slice(5) }] } }
        : {}),
    })),
  }
}

// Colors of the weather variables (fixed per variable across charts).
export const WEATHER_COLORS = {
  rain: '#2563eb', rain30: '#93c5fd', vpd: '#ea580c', tmax: '#dc2626', tmin: '#f59e0b', dew: '#0891b2',
  rh: '#0d9488', wind: '#7c3aed',
}

// Compact chart of the period ficha: daily rain (mm) vs. the daily maximum VPD (kPa).
export const weatherChart = (days) =>
  dailyChart(days.map((d) => d.date), [
    { name: 'Lluvia', type: 'bar', data: days.map((d) => d.precipitation_mm), color: WEATHER_COLORS.rain, unit: 'mm' },
    { name: 'VPD máx', type: 'line', axis: 1, data: days.map((d) => d.vpd_max_kpa), color: WEATHER_COLORS.vpd, unit: 'kPa', digits: 2 },
  ], { axes: ['mm', 'kPa'] })

export const monthLabel = (iso) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(2, 4)}`
