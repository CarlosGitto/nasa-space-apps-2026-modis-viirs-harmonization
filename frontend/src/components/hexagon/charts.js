// Chart options shared by the hexagon fichas. One bar series per sensor, side by
// side: MODIS and VIIRS are never summed. Texts and numbers in the user's language;
// font sizes follow the accessibility text size.
import { SENSOR_COLORS, SENSOR_LABELS } from '../../mapShared'
import { i18n } from '../../i18n'
import { date, monthName, num, pct } from '../../format'
import { usePrefsStore } from '../../stores/prefs'

export { monthName, num, pct } from '../../format'

const TEXT = '#1f2937'
const MUTED = '#6b7280'
const GRID = '#e5e7eb'
const SCALE = { normal: 1, large: 1.25, xlarge: 1.5 }

// Chart font size in px for the current text size (charts draw on a canvas: rem doesn't apply).
export const chartFont = (px) => Math.round(px * (SCALE[usePrefsStore().textSize] ?? 1))

// categories: x labels; bySensor: { modis: [n per category], viirs: [...] }
export function sensorBars(categories, bySensor, { labelInterval = 'auto', decimals = 0, format = null, max = null } = {}) {
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
      valueFormatter: format ?? ((v) => t('common.detections', { n: num(v, decimals) })),
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
      minInterval: decimals || max ? 0 : 1,
      ...(max ? { min: 0, max } : {}),
      axisLabel: { color: MUTED, fontSize: chartFont(10), formatter: (v) => num(v, Number.isInteger(v) ? 0 : 1) },
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
// its highest bar. decimals: 1 for harmonized (fractional) counts.
export function chartLabel(title, categories, bySensor, decimals = 0) {
  const t = i18n.global.t
  const parts = Object.entries(bySensor).map(([sensor, values]) => {
    const total = values.reduce((a, b) => a + b, 0)
    if (!total) return `${SENSOR_LABELS[sensor] ?? sensor}: ${t('chart.empty')}`
    const max = Math.max(...values)
    return t('chart.sensorSummary', {
      sensor: SENSOR_LABELS[sensor] ?? sensor, total: num(total, decimals), max: num(max, decimals), when: categories[values.indexOf(max)],
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

// Daily chart with up to two value axes: bars and lines over the same days.
// series: [{ name, type: 'bar' | 'line', data, axis: 0 | 1, color, unit, digits, stack }]
// The unit goes in the legend ("VPD máx · kPa"), not as an axis name: axis names sat right
// under the legend and overlapped it. axes: 1 or 2 value axes; scale: don't force zero
// (temperatures); mark: a 'YYYY-MM-DD' to highlight (the time slider's day).
export function dailyChart(dates, series, { axes = 1, scale = false, mark = null, tooltipExtra = null } = {}) {
  const categories = dates.map(dayLabel)
  const axis = (i) => ({
    type: 'value', scale,
    axisLabel: { color: MUTED, fontSize: chartFont(10), hideOverlap: true, formatter: (v) => num(v, 1) },
    splitLine: { show: i === 0, lineStyle: { color: GRID } },
  })
  const legendName = (s) => (s.unit ? `${s.name} · ${s.unit}` : s.name)
  return {
    animation: false,
    grid: { left: chartFont(34), right: axes > 1 ? chartFont(34) : 8, top: chartFont(30), bottom: chartFont(22), containLabel: false },
    legend: {
      top: 0, left: 0, right: 0, itemWidth: 10, itemHeight: 8, itemGap: 10, icon: 'roundRect',
      textStyle: { color: TEXT, fontSize: chartFont(11) },
    },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontSize: chartFont(12) },
      formatter: (items) => {
        const day = date(dates[items[0].dataIndex], 'long')
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
      axisLabel: { color: MUTED, fontSize: chartFont(10), hideOverlap: true },
      axisLine: { lineStyle: { color: GRID } }, axisTick: { show: false },
    },
    yAxis: Array.from({ length: axes }, (_, i) => axis(i)),
    series: series.map((s, i) => ({
      name: legendName(s), type: s.type, data: s.data, yAxisIndex: s.axis ?? 0, stack: s.stack,
      itemStyle: { color: s.color, borderRadius: s.type === 'bar' ? [3, 3, 0, 0] : 0 },
      ...(s.type === 'bar'
        ? { barMaxWidth: 10 }
        : { showSymbol: false, connectNulls: false, smooth: 0.2, lineStyle: { color: s.color, width: 2, type: s.dashed ? 'dashed' : 'solid' } }),
      // The time slider's day, once per chart (by position: the axis shows localized labels).
      ...(i === 0 && mark && dates.includes(mark)
        ? { markLine: { symbol: 'none', silent: true, label: { show: false }, lineStyle: { color: MUTED, type: 'dashed' },
                        data: [{ xAxis: dates.indexOf(mark) }] } }
        : {}),
    })),
  }
}

// Text alternative of a dailyChart for screen readers: per series, its lowest and highest
// value (or its total, for bars of amounts like rain).
export function dailyChartLabel(title, dates, series) {
  const t = i18n.global.t
  const parts = series.map((s) => {
    const values = s.data.filter((v) => v !== null && v !== undefined)
    if (!values.length) return `${s.name}: ${t('chart.empty')}`
    const fmtv = (v) => `${num(v, s.digits ?? 1)} ${s.unit ?? ''}`.trim()
    return s.type === 'bar'
      ? t('chart.seriesTotal', { name: s.name, total: fmtv(values.reduce((a, b) => a + b, 0)) })
      : t('chart.seriesRange', { name: s.name, min: fmtv(Math.min(...values)), max: fmtv(Math.max(...values)) })
  })
  const span = dates.length ? `${date(dates[0])} – ${date(dates.at(-1))}` : ''
  return t('chart.label', { title: `${title} (${span})`, summary: parts.join('; ') })
}

// Colors of the weather variables, the same in every chart. Where fire is in the same
// chart (VPD next to MODIS orange / VIIRS blue bars) they avoid fire colors; the
// temperature chart, fire-free, uses the usual red (max) / blue (min).
export const WEATHER_COLORS = {
  rain: '#60a5fa', rain30: '#1e40af', vpd: '#7c3aed', tmax: '#dc2626', tmin: '#3b82f6', dew: '#14b8a6',
  rh: '#0d9488', wind: '#475569',
}

// Compact chart of the period ficha: daily rain (mm) vs. the daily maximum VPD (kPa).
export const weatherSeries = (days) => [
  { name: i18n.global.t('weather.rain'), type: 'bar', data: days.map((d) => d.precipitation_mm), color: WEATHER_COLORS.rain, unit: 'mm' },
  { name: i18n.global.t('weather.vpdMax'), type: 'line', axis: 1, data: days.map((d) => d.vpd_max_kpa), color: WEATHER_COLORS.vpd, unit: 'kPa', digits: 2 },
]
export const weatherChart = (days) => dailyChart(days.map((d) => d.date), weatherSeries(days), { axes: 2 })

// Series spliced across the MODIS -> VIIRS change (fct_hexagon_harmonized_monthly), in MODIS units.
// data: { months, covered_months } from the API. A month without a row is a zero only where the data was downloaded
// (covered_months); elsewhere it is a gap (null), never a zero. The monthly value is noisy (a cell has few
// detections): the 12-month mean is drawn on top, over covered months only.
export const SPLICE_MONTH = '2012-02-01'
function harmonizedSeries(data, metric) {
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
  const unit = metric === 'frp' ? 'MW' : i18n.global.t('harm.detectionsUnit')
  return { categories, monthly, trend, unit }
}

export function harmonizedLine(data, metric) {
  const s = harmonizedSeries(data, metric)
  if (!s) return null
  const t = i18n.global.t
  const spliceAt = s.categories.indexOf(SPLICE_MONTH)
  return {
    animation: false,
    grid: { left: chartFont(44), right: 8, top: chartFont(28), bottom: chartFont(22) },
    legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 8, textStyle: { color: TEXT, fontSize: chartFont(11) } },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontSize: chartFont(12) },
      valueFormatter: (v) => (v === null || v === undefined ? '–' : `${num(v, 1)} ${s.unit}`),
    },
    xAxis: {
      type: 'category',
      data: s.categories.map(monthLabel),
      axisLabel: { color: MUTED, fontSize: chartFont(10), hideOverlap: true },
      axisLine: { lineStyle: { color: GRID } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: MUTED, fontSize: chartFont(10), formatter: (v) => num(v, Number.isInteger(v) ? 0 : 1) },
      splitLine: { lineStyle: { color: GRID } },
    },
    series: [
      {
        name: t('harm.monthly'), type: 'line', data: s.monthly, showSymbol: false,
        lineStyle: { width: 1, color: '#9ca3af' }, itemStyle: { color: '#9ca3af' },
        markLine: spliceAt < 0 ? undefined : {
          symbol: 'none', silent: true,
          lineStyle: { color: MUTED, type: 'dashed' },
          label: { formatter: 'MODIS → VIIRS', color: MUTED, fontSize: chartFont(10) },
          data: [{ xAxis: spliceAt }],
        },
      },
      {
        name: t('harm.mean12'), type: 'line', data: s.trend, showSymbol: false, smooth: true,
        lineStyle: { width: 2, color: '#eb6834' }, itemStyle: { color: '#eb6834' },
      },
    ],
  }
}

// Text alternative of harmonizedLine for screen readers: the span, the highest month and the
// range of the 12-month mean.
export function harmonizedLabel(title, data, metric) {
  const t = i18n.global.t
  const s = harmonizedSeries(data, metric)
  if (!s) return t('chart.label', { title, summary: t('chart.empty') })
  const values = s.monthly.filter((v) => v !== null)
  const peak = Math.max(...values)
  const trend = s.trend.filter((v) => v !== null)
  const fmt = (v) => `${num(v, 1)} ${s.unit}`
  const summary = [
    t('harm.labelSpan', { start: monthLabel(s.categories[0]), end: monthLabel(s.categories.at(-1)) }),
    t('harm.labelPeak', { value: fmt(peak), month: monthLabel(s.categories[s.monthly.indexOf(peak)]) }),
    ...(trend.length ? [t('harm.labelTrend', { min: fmt(Math.min(...trend)), max: fmt(Math.max(...trend)) })] : []),
  ]
  return t('chart.label', { title, summary: summary.join('; ') })
}

// Reliability of the detections by year and sensor (fct_hexagon_reliability_monthly): months are weighted by their
// detections, so a month with two detections does not count as much as one with two hundred.
// metric: 'reliability' (mean probability that an independent sensor confirms) | 'persistent' (share of persistent detections)
const reliabilityValue = (v, metric) => (v === null || v === undefined
  ? i18n.global.t('chart.empty')
  : metric === 'reliability'
    ? i18n.global.t('harm.reliability.meanValue', { v: num(v, 2) })
    : i18n.global.t('harm.reliability.persistentValue', { v: pct(v) }))

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
  return sensorBars(span.map(String), bySensor, {
    labelInterval: 0, max: 1, format: (v) => reliabilityValue(v, metric),
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

// Text alternative of reliabilityByYear for screen readers: each sensor's overall value and its years.
export function reliabilityLabel(title, rows, metric) {
  const t = i18n.global.t
  const years = rows.map((r) => r.month.slice(0, 4)).sort()
  const parts = [...new Set(rows.map((r) => r.sensor))].map((sensor) => {
    const tot = reliabilityTotals(rows, sensor)
    return `${SENSOR_LABELS[sensor] ?? sensor}: ${reliabilityValue(tot && tot[metric], metric)}`
  })
  const span = years.length ? ` (${years[0]} – ${years.at(-1)})` : ''
  return t('chart.label', { title: `${title}${span}`, summary: parts.join('; ') || t('chart.empty') })
}
