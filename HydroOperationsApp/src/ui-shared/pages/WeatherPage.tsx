import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CloudSun, Eye, RefreshCw } from 'lucide-react'
import { queryWeatherData, type WeatherAreaMetric, type WeatherData, type WeatherForecast, type WeatherObservation } from '../../services/fabric'
import { WeatherMap, type WeatherSelection } from '../components/weather/WeatherMap'

const RANGES = [6, 12, 24, 48, 72]
const VARIABLE_LABELS: Record<string, string> = {
  precipitation: 'Rain', pressure: 'Pressure', temperature: 'Temperature', relative_humidity: 'Humidity',
  dew_point: 'Dew point', solar_radiation: 'Sunlight', wind_speed: 'Wind', wind_gust: 'Gust', wind_direction: 'Direction',
}
const VARIABLE_ORDER = ['temperature', 'precipitation', 'pressure', 'relative_humidity', 'dew_point', 'solar_radiation', 'wind_speed', 'wind_gust', 'wind_direction']
const PRIMARY_VARIABLES = new Set(['temperature', 'precipitation'])
const variableRank = (variableId: string) => {
  const index = VARIABLE_ORDER.indexOf(variableId)
  return index < 0 ? VARIABLE_ORDER.length : index
}

type TimelineValue = { variableId: string; value?: number; unit: string; volume?: number }
type TimelineRow = { timestamp: string; source: string; values: TimelineValue[] }
type WeatherTableRow = TimelineRow & { kind: 'observation' | 'forecast' }
type PrecipitationSummary = { amount?: number; unit: string; volume?: number }

function formatValue(item: TimelineValue) {
  if (item.value == null || !Number.isFinite(Number(item.value))) return '—'
  const value = Number(item.value)
  const digits = item.variableId === 'wind_direction' ? 0 : Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${item.unit}`
}

function groupRows(items: Array<WeatherObservation | WeatherForecast | WeatherAreaMetric>, timestampOf: (item: typeof items[number]) => string): TimelineRow[] {
  const groups = new Map<string, TimelineRow>()
  for (const item of items) {
    const timestamp = timestampOf(item)
    const current = groups.get(timestamp) ?? { timestamp, source: item.source_id, values: [] }
    current.values.push({
      variableId: item.variable_id,
      value: 'area_id' in item ? item.area_weighted_value : item.value,
      unit: item.unit,
      volume: 'rainfall_volume_m3' in item ? item.rainfall_volume_m3 : undefined,
    })
    groups.set(timestamp, current)
  }
  return [...groups.values()]
    .map(row => ({ ...row, values: row.values.sort((left, right) => variableRank(left.variableId) - variableRank(right.variableId)) }))
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))
}

function recentLocationObservations(items: WeatherObservation[]): TimelineRow[] {
  const primary = items.filter(item => item.source_id.toLowerCase() !== 'ukmet')
  const hasPrimaryRainfall = primary.some(item => item.variable_id === 'precipitation')
  const fallback = items.filter(item => item.source_id.toLowerCase() === 'ukmet' && (!primary.length || (!hasPrimaryRainfall && item.variable_id === 'precipitation')))
  return groupRows([...primary, ...fallback], item => (item as WeatherObservation).observed_at_utc).slice(-3)
}

export function WeatherPage() {
  const [weather, setWeather] = useState<WeatherData>()
  const [selection, setSelection] = useState<WeatherSelection>()
  const [rangeHours, setRangeHours] = useState(24)
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable' | 'error'>('loading')
  const [error, setError] = useState<string>()
  const [timeAnchor, setTimeAnchor] = useState(() => Date.now())
  const [forecastVendor, setForecastVendor] = useState('')
  const [showMoreVariables, setShowMoreVariables] = useState(false)

  const applyWeather = (data: WeatherData) => {
    setWeather(data)
    setSelection(current => current ?? (data.locations[0]
      ? { kind: 'location', id: data.locations[0].location_id }
      : data.areas[0] ? { kind: 'area', id: data.areas[0].area_id } : undefined))
    setTimeAnchor(Date.now())
    setState('ready')
  }

  const load = async () => {
    setState('loading'); setError(undefined)
    try {
      const data = await queryWeatherData(true)
      if (!data) { setState('unavailable'); return }
      applyWeather(data)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Weather data is unavailable.')
      setState('error')
    }
  }

  useEffect(() => {
    let cancelled = false
    void queryWeatherData().then(data => {
      if (cancelled) return
      if (data) applyWeather(data)
      else setState('unavailable')
    }).catch(reason => {
      if (cancelled) return
      setError(reason instanceof Error ? reason.message : 'Weather data is unavailable.')
      setState('error')
    })
    return () => { cancelled = true }
  }, [])

  const selectedName = selection?.kind === 'location'
    ? weather?.locations.find(item => item.location_id === selection.id)?.location_name
    : weather?.areas.find(item => item.area_id === selection?.id)?.area_name

  const forecastVendors = useMemo(() => {
    if (!weather) return []
    return [...new Set([
      ...weather.forecasts.map(item => item.source_id),
      ...weather.areaMetrics.filter(item => item.data_kind === 'forecast').map(item => item.source_id),
    ].filter(source => source.toLowerCase() !== 'ukmet'))].sort()
  }, [weather])
  const selectedVendor = forecastVendors.includes(forecastVendor) ? forecastVendor : forecastVendors[0] || ''

  const timelines = useMemo(() => {
    if (!weather || !selection) return { observations: [], forecasts: [] }
    const now = timeAnchor
    const duration = rangeHours * 3_600_000
    const previousDay = 24 * 3_600_000
    if (selection.kind === 'location') {
      const observations = weather.observations.filter(item => item.location_id === selection.id && Date.parse(item.observed_at_utc) >= now - previousDay && Date.parse(item.observed_at_utc) <= now)
      const vendorForecasts = weather.forecasts.filter(item => item.location_id === selection.id && item.source_id === selectedVendor)
      const latestIssue = Math.max(0, ...vendorForecasts.map(item => Date.parse(item.reference_time_utc)))
      const forecasts = vendorForecasts.filter(item => Date.parse(item.reference_time_utc) === latestIssue && Date.parse(item.valid_time_utc) >= now && Date.parse(item.valid_time_utc) <= now + duration)
      return {
        observations: recentLocationObservations(observations),
        forecasts: groupRows(forecasts, item => (item as WeatherForecast).valid_time_utc),
      }
    }
    const metrics = weather.areaMetrics.filter(item => item.area_id === selection.id)
    const observations = metrics.filter(item => item.data_kind === 'observation' && Date.parse(item.valid_time_utc) >= now - previousDay && Date.parse(item.valid_time_utc) <= now)
    const vendorForecasts = metrics.filter(item => item.data_kind === 'forecast' && item.source_id === selectedVendor)
    const latestIssue = Math.max(0, ...vendorForecasts.map(item => Date.parse(item.reference_time_utc ?? '')))
    const forecasts = vendorForecasts.filter(item => Date.parse(item.reference_time_utc ?? '') === latestIssue && Date.parse(item.valid_time_utc) >= now && Date.parse(item.valid_time_utc) <= now + duration)
    return {
      observations: groupRows(observations, item => (item as WeatherAreaMetric).valid_time_utc).slice(-3),
      forecasts: groupRows(forecasts, item => (item as WeatherAreaMetric).valid_time_utc),
    }
  }, [rangeHours, selectedVendor, selection, timeAnchor, weather])

  const precipitationSummary = useMemo<PrecipitationSummary | undefined>(() => {
    if (!weather || !selection || !selectedVendor) return undefined
    const end = timeAnchor + 24 * 3_600_000
    if (selection.kind === 'location') {
      const precipitation = weather.forecasts.filter(item => item.location_id === selection.id && item.source_id === selectedVendor && item.variable_id === 'precipitation')
      const latestIssue = Math.max(0, ...precipitation.map(item => Date.parse(item.reference_time_utc)))
      const values = precipitation.filter(item => Date.parse(item.reference_time_utc) === latestIssue && Date.parse(item.valid_time_utc) > timeAnchor && Date.parse(item.valid_time_utc) <= end)
      return { amount: values.length ? values.reduce((sum, item) => sum + Number(item.value || 0), 0) : undefined, unit: values[0]?.unit ?? 'mm' }
    }
    const precipitation = weather.areaMetrics.filter(item => item.area_id === selection.id && item.source_id === selectedVendor && item.data_kind === 'forecast' && item.variable_id === 'precipitation')
    const latestIssue = Math.max(0, ...precipitation.map(item => Date.parse(item.reference_time_utc ?? '')))
    const values = precipitation.filter(item => Date.parse(item.reference_time_utc ?? '') === latestIssue && Date.parse(item.valid_time_utc) > timeAnchor && Date.parse(item.valid_time_utc) <= end)
    return {
      amount: values.length ? values.reduce((sum, item) => sum + Number(item.area_weighted_value || 0), 0) : undefined,
      unit: values[0]?.unit ?? 'mm',
      volume: values.some(item => item.rainfall_volume_m3 != null) ? values.reduce((sum, item) => sum + Number(item.rainfall_volume_m3 || 0), 0) : undefined,
    }
  }, [selectedVendor, selection, timeAnchor, weather])

  return <div className="weather-page">
    <section className="weather-head">
      <div><span className="v2-eyebrow">Weather Operations</span><h1>Stations and local-area outlook</h1><p>Observed conditions and 72-hour Aurora forecasts at each asset and within its 20 km operating area.</p></div>
      <div className="weather-actions">
        <label>Location<select value={selection ? `${selection.kind}:${selection.id}` : ''} onChange={event => {
          const [kind, ...id] = event.target.value.split(':')
          if ((kind === 'location' || kind === 'area') && id.length) setSelection({ kind, id: id.join(':') })
        }}><option value="" disabled>Select</option>{weather?.locations.map(item => <option value={`location:${item.location_id}`} key={`location:${item.location_id}`}>{item.location_name}</option>)}{weather?.areas.map(item => <option value={`area:${item.area_id}`} key={`area:${item.area_id}`}>{item.area_name}</option>)}</select></label>
        <label>Vendor<select value={selectedVendor} onChange={event => setForecastVendor(event.target.value)} disabled={!forecastVendors.length}>{forecastVendors.map(vendor => <option value={vendor} key={vendor}>{vendor === 'aurora' ? 'Aurora' : vendor === 'ukmet' ? 'UKMet' : vendor}</option>)}</select></label>
        <label>Window<select value={rangeHours} onChange={event => setRangeHours(Number(event.target.value))}>{RANGES.map(value => <option value={value} key={value}>{value} hours</option>)}</select></label>
        <button className="v2-icon-action" type="button" onClick={() => void load()} disabled={state === 'loading'} title="Refresh weather data" aria-label="Refresh weather data"><RefreshCw size={16} className={state === 'loading' ? 'spin' : undefined} /></button>
      </div>
    </section>

    {error && <div className="v2-notice" role="alert"><AlertTriangle size={15} /><span>{error}</span></div>}
    <section className="weather-workspace">
      <article className="weather-map-panel">
        <div className="weather-map-stage">
          {weather ? <WeatherMap locations={weather.locations} areas={weather.areas} selection={selection} onSelect={setSelection} /> : <div className="weather-map-empty" role="status" aria-live="polite">{state === 'loading' ? 'Loading weather map…' : <><span>No weather data yet. In Administration, run Connect weather, then Seed &amp; provision to publish the GraphQL API.</span><button type="button" onClick={() => void load()}>Retry</button></>}</div>}
          {selection && <div className="weather-precipitation-summary" aria-live="polite"><span>Next 24h precipitation</span><strong>{precipitationSummary?.amount == null ? 'No forecast' : `${precipitationSummary.amount.toFixed(1)} ${precipitationSummary.unit}`}</strong>{precipitationSummary?.volume != null && <small>{Math.round(precipitationSummary.volume).toLocaleString()} m³ over area</small>}</div>}
          <div className="weather-legend"><span><i className="station" />Station</span><span><i className="area" />Area</span></div>
        </div>
        <WeatherChart name={selectedName} rows={timelines.forecasts} rangeHours={rangeHours} />
      </article>
      <aside className="weather-detail" aria-live="polite">
        <div className="weather-detail-head"><span className="weather-detail-icon"><CloudSun size={18} /></span><div><span>{selection?.kind === 'area' ? 'Weather area' : 'Weather station'}</span><h2>{selectedName ?? 'Select a station or area'}</h2></div><button className="weather-more" type="button" aria-expanded={showMoreVariables} onClick={() => setShowMoreVariables(current => !current)}>{showMoreVariables ? 'Less…' : 'More…'}</button></div>
        <WeatherValuesTable observations={timelines.observations} forecasts={timelines.forecasts} showMoreVariables={showMoreVariables} />
      </aside>
    </section>
  </div>
}

function WeatherValuesTable({ observations, forecasts, showMoreVariables }: { observations: TimelineRow[]; forecasts: TimelineRow[]; showMoreVariables: boolean }) {
  const rows: WeatherTableRow[] = [
    ...observations.map(row => ({ ...row, kind: 'observation' as const })),
    ...forecasts.map(row => ({ ...row, kind: 'forecast' as const })),
  ]
  const secondaryVariables = [...new Set(rows.flatMap(row => row.values.map(value => value.variableId)).filter(variableId => !PRIMARY_VARIABLES.has(variableId)))]
    .sort((left, right) => variableRank(left) - variableRank(right) || left.localeCompare(right))
  const visibleVariables = showMoreVariables ? secondaryVariables : []

  return <div className="weather-values-table-wrap">
    <table className="weather-values-table" aria-label="Observed and forecast weather values">
      <thead><tr><th><span className="sr-only">Type</span></th><th>Date</th><th>Time</th><th>Rainfall</th><th>Temperature</th>{visibleVariables.map(variableId => <th key={variableId}>{VARIABLE_LABELS[variableId] ?? variableId}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={`${row.kind}-${row.timestamp}`}>
        <td><span className={`weather-row-kind ${row.kind}`} title={row.kind === 'observation' ? 'Observation' : 'Forecast'}>{row.kind === 'observation' ? <Eye size={14} aria-hidden="true" /> : <CloudSun size={14} aria-hidden="true" />}<span className="sr-only">{row.kind === 'observation' ? 'Observation' : 'Forecast'}</span></span></td>
        <td><time dateTime={row.timestamp}>{new Date(row.timestamp).toLocaleDateString([], { month: 'short', day: 'numeric' })}</time></td>
        <td>{new Date(row.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
        <td><WeatherValue value={row.values.find(value => value.variableId === 'precipitation')} /></td>
        <td><WeatherValue value={row.values.find(value => value.variableId === 'temperature')} /></td>
        {visibleVariables.map(variableId => <td key={variableId}><WeatherValue value={row.values.find(value => value.variableId === variableId)} /></td>)}
      </tr>)}{!rows.length && <tr><td className="weather-table-empty" colSpan={5 + visibleVariables.length}>No observations or forecast values in this window.</td></tr>}</tbody>
    </table>
  </div>
}

function WeatherValue({ value }: { value?: TimelineValue }) {
  if (!value) return <>—</>
  return <><strong>{formatValue(value)}</strong>{value.volume != null && <small>{Math.round(value.volume).toLocaleString()} m³</small>}</>
}

function WeatherChart({ name, rows, rangeHours }: { name?: string; rows: TimelineRow[]; rangeHours: number }) {
  const data = rows.map(row => ({
    timestamp: row.timestamp,
    temperature: row.values.find(value => value.variableId === 'temperature')?.value,
    rainfall: row.values.find(value => value.variableId === 'precipitation')?.value,
  })).filter(item => item.temperature != null || item.rainfall != null).sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))

  const width = 720
  const height = 210
  const plot = { left: 46, right: 52, top: 18, bottom: 34 }
  const plotWidth = width - plot.left - plot.right
  const plotHeight = height - plot.top - plot.bottom
  const temperatures = data.flatMap(item => item.temperature == null ? [] : [Number(item.temperature)])
  const rainfall = data.flatMap(item => item.rainfall == null ? [] : [Number(item.rainfall)])
  const temperatureMin = temperatures.length ? Math.floor(Math.min(...temperatures) - 1) : 0
  const temperatureMax = temperatures.length ? Math.ceil(Math.max(...temperatures) + 1) : 1
  const temperatureRange = Math.max(1, temperatureMax - temperatureMin)
  const rainfallMax = Math.max(1, ...rainfall)
  const x = (index: number) => data.length < 2 ? plot.left + plotWidth / 2 : plot.left + index * plotWidth / (data.length - 1)
  const temperatureY = (value: number) => plot.top + (temperatureMax - value) / temperatureRange * plotHeight
  const rainfallY = (value: number) => plot.top + (rainfallMax - value) / rainfallMax * plotHeight
  const barWidth = Math.min(28, plotWidth / Math.max(1, data.length) * .55)
  const temperaturePoints = data.flatMap((item, index) => item.temperature == null ? [] : [`${x(index)},${temperatureY(Number(item.temperature))}`]).join(' ')
  const labelIndexes = new Set([0, Math.floor((data.length - 1) / 2), data.length - 1])

  return <section className="weather-chart-panel" aria-label={`Temperature and rainfall forecast for ${name ?? 'selected weather location'}`}>
    <div className="weather-chart-head"><div><span>Forecast chart</span><h3>{name ?? 'Select a station or area'}</h3></div><div className="weather-chart-legend"><span className="temperature">Temperature</span><span className="rainfall">Rainfall</span></div></div>
    {data.length ? <svg className="weather-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Temperature line and rainfall bars for the next ${rangeHours} hours`}>
      {[0, .5, 1].map(ratio => <g key={ratio}><line className="weather-chart-grid" x1={plot.left} x2={width - plot.right} y1={plot.top + ratio * plotHeight} y2={plot.top + ratio * plotHeight} /><text className="weather-chart-axis" x={plot.left - 7} y={plot.top + ratio * plotHeight + 3} textAnchor="end">{(temperatureMax - ratio * temperatureRange).toFixed(0)}°</text><text className="weather-chart-axis rainfall-axis" x={width - plot.right + 7} y={plot.top + ratio * plotHeight + 3}>{(rainfallMax - ratio * rainfallMax).toFixed(1)}</text></g>)}
      {data.map((item, index) => item.rainfall == null ? null : <rect className="weather-chart-rain" key={`rain-${item.timestamp}`} x={x(index) - barWidth / 2} y={rainfallY(Number(item.rainfall))} width={barWidth} height={plot.top + plotHeight - rainfallY(Number(item.rainfall))}><title>{`${new Date(item.timestamp).toLocaleString()}: ${Number(item.rainfall).toFixed(1)} mm rainfall`}</title></rect>)}
      {temperaturePoints && <polyline className="weather-chart-temperature-line" points={temperaturePoints} />}
      {data.map((item, index) => item.temperature == null ? null : <circle className="weather-chart-temperature-point" key={`temperature-${item.timestamp}`} cx={x(index)} cy={temperatureY(Number(item.temperature))} r="3"><title>{`${new Date(item.timestamp).toLocaleString()}: ${Number(item.temperature).toFixed(1)} °C`}</title></circle>)}
      {data.map((item, index) => labelIndexes.has(index) ? <text className="weather-chart-axis weather-chart-time" key={`time-${item.timestamp}`} x={x(index)} y={height - 10} textAnchor={index === 0 ? 'start' : index === data.length - 1 ? 'end' : 'middle'}>{new Date(item.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit' })}</text> : null)}
      <text className="weather-chart-unit" x="8" y="11">°C</text><text className="weather-chart-unit" x={width - 8} y="11" textAnchor="end">mm</text>
    </svg> : <p className="weather-chart-empty">No temperature or rainfall forecast in this window.</p>}
  </section>
}