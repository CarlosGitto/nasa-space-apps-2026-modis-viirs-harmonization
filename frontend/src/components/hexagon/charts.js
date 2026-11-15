// Chart options shared by the hexagon fichas. One bar series per sensor, side by
// side: MODIS and VIIRS are never summed. Texts and numbers in the user's language;
// font sizes follow the accessibility text size.
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import { i18n } from '../../i18n'
import { date, monthName, num } from '../../format'
import { usePrefsStore } from '../../stores/prefs'

export { monthName, num, pct } from '../../format'

const TEXT = '#1f2937'
const MUTED = '#6b7280'
const GRID = '#e5e7eb'
const SCALE = { normal: 1, large: 1.25, xlarge: 1.5 }

// Chart font size in px for the current text size (charts draw on a canvas: rem doesn't apply).
export const chartFont = (px) => Math.round(px * (SCALE[usePrefsStore().textSize] ?? 1))

// categories: x labels; bySensor: { modis: [n per category], viirs: [...] }
export function sensorBars(categories, bySensor, { labelInterval = 'auto' } = {}) {
  const sensors = Object.keys(bySensor)
  const t = i18n.global.t
  return {
    animation: false,
    grid: { left: chartFont(40), right: 8, top: sensors.length > 1 ? chartFont(28) : 10, bottom: chartFont(22) },
    legend: sensors.length > 1
      ? { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: TEXT, fontSize: chartFont(11) } }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      textStyle: { fontSize: chartFont(12) },
      valueFormatter: (v) => t('common.detections', { n: num(v) }),
    },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { color: MUTED, fontSize: chartFont(10), interval: labelInterval, hideOverlap: true },
      axisLine: { lineStyle: { color: GRID } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: MUTED, fontSize: chartFont(10), formatter: (v) => num(v) },
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

// Text alternative of a sensorBars chart for screen readers: per sensor, its total and
// its highest bar.
export function chartLabel(title, categories, bySensor) {
  const t = i18n.global.t
  const parts = Object.entries(bySensor).map(([sensor, values]) => {
    const total = values.reduce((a, b) => a + b, 0)
    if (!total) return `${SENSOR_LABELS[sensor] ?? sensor}: ${t('chart.empty')}`
    const max = Math.max(...values)
    return t('chart.sensorSummary', {
      sensor: SENSOR_LABELS[sensor] ?? sensor, total: num(total), max: num(max), when: categories[values.indexOf(max)],
    })
  })
  return t('chart.label', { title, summary: parts.join('; ') })
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

export const months = () => Array.from({ length: 12 }, (_, i) => monthName(i + 1))
export const monthLabel = (iso) => date(iso, 'monthYear')
export const dayLabel = (iso) => date(iso, 'dayMonth')
