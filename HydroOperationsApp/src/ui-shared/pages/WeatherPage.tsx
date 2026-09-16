import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CloudSun, Eye, RefreshCw } from 'lucide-react'
import { queryWeatherData, type WeatherData, type WeatherForecast, type WeatherObservation } from '../../services/fabric'
import { WeatherMap, type WeatherSelection } from '../components/weather/WeatherMap'
import { summarizePrecipitation } from '../weatherSummary'

const RANGES = [6, 12, 24, 48, 72]
const VARIABLE_LABELS: Record<string, string> = {
  precipitation: 'Rain', pressure: 'Pressure', temperature: 'Temperature', relative_humidity: 'Humidity',
  dew_point: 'Dew point', solar_radiation: 'Sunlight', wind_speed: 'Wind', wind_gust: 'Gust', wind_direction: 'Direction',
}
const VARIABLE_ORDER = ['temperature', 'precipitation', 'pressure', 'relative_humidity', 'dew_point', 'solar_radiation', 'wind_speed', 'wind_gust', 'wind_direction']
const PRIMARY_VARIABLES = new Set(['temperature', 'precipitation'])
const FALLBACK_UNITS: Record<string, string> = {
  precipitation: 'mm', temperature: '°C', pressure: 'hPa', relative_humidity: '%',
  dew_point: '°C', solar_radiation: 'W/m2', wind_speed: 'm/s', wind_gust: 'm/s', wind_direction: '°',
}
const variableRank = (variableId: string) => {
  const index = VARIABLE_ORDER.indexOf(variableId)
  return index < 0 ? VARIABLE_ORDER.length : index
}
const byTimestamp = (left: { timestamp: string }, right: { timestamp: string }) => Date.parse(left.timestamp) - Date.parse(right.timestamp)

type TimelineValue = { variableId: string; value?: number; unit: string; volume?: number }
type TimelineRow = { timestamp: string; source: string; values: TimelineValue[] }
type WeatherTableRow = TimelineRow & { kind: 'observation' | 'forecast' }
type PrecipitationSummary = { amount?: number; unit: string; volume?: number; coveredHours: number; windowHours: number }

function formatValue(item: TimelineValue) {
  if (item.value == null || !Number.isFinite(Number(item.value))) return '—'
  const value = Number(item.value)
  const digits = item.variableId === 'wind_direction' ? 0 : Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${item.unit}`
}

/** Serving rows are already pivoted, so a row maps straight to a timeline entry. */
function toTimelineRow(row: WeatherForecast | WeatherObservation, timestamp: string, unitOf: (variableId: string) => string): TimelineRow {
  return {
    timestamp,
    source: row.source_id,
    values: VARIABLE_ORDER.flatMap(variableId => {
      const value = (row as Record<string, unknown>)[variableId]
      return value == null ? [] : [{ variableId, value: Number(value), unit: unitOf(variableId) }]
    }),
  }
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

  const unitOf = useMemo(() => {
    const units = new Map(weather?.variables.map(item => [item.variable_id, item.canonical_unit]))
    return (variableId: string) => units.get(variableId) ?? FALLBACK_UNITS[variableId] ?? ''
  }, [weather])

  const forecastVendors = useMemo(
    () => [...new Set(weather?.forecasts.map(item => item.source_id) ?? [])].sort(),
    [weather],
  )
  const selectedVendor = forecastVendors.includes(forecastVendor) ? forecastVendor : forecastVendors[0] || ''

  // An area reports observations through the station its metadata names as representative.
  const observedLocationId = useMemo(() => {
    if (!selection) return undefined
    if (selection.kind === 'location') return selection.id
    const metadata = weather?.areas.find(item => item.area_id === selection.id)?.metadata_json
    if (!metadata) return undefined
    try { return JSON.parse(metadata).location_id as string | undefined } catch { return undefined }
  }, [selection, weather])

  const selectedForecasts = useMemo(() => {
    if (!weather || !selection || !selectedVendor) return []
    return weather.forecasts
      .filter(item => item.target_kind === selection.kind && item.target_id === selection.id && item.source_id === selectedVendor)
      .sort((left, right) => Date.parse(left.valid_time_utc) - Date.parse(right.valid_time_utc))
  }, [selectedVendor, selection, weather])

  const timelines = useMemo(() => {
    if (!weather) return { observations: [], forecasts: [] }
    const now = timeAnchor
    const end = now + rangeHours * 3_600_000
    const observations = observedLocationId
      ? weather.observations
        .filter(item => item.location_id === observedLocationId && Date.parse(item.observed_at_utc) >= now - 24 * 3_600_000 && Date.parse(item.observed_at_utc) <= now)
        .map(item => toTimelineRow(item, item.observed_at_utc, unitOf))
        .sort(byTimestamp)
      : []
    return {
      observations,
      forecasts: selectedForecasts
        .filter(item => Date.parse(item.valid_time_utc) >= now && Date.parse(item.valid_time_utc) <= end)
        .map(item => toTimelineRow(item, item.valid_time_utc, unitOf)),
    }
  }, [observedLocationId, rangeHours, selectedForecasts, timeAnchor, unitOf, weather])

  // Rainfall over a window is the difference of two running totals, not a sum of rows,
  // because a row only tiles the window when its interval matches the valid-time spacing.
  const precipitationSummary = useMemo<PrecipitationSummary | undefined>(() => {
    if (!selectedForecasts.length) return undefined
    const windowHours = 24
    const summary = summarizePrecipitation(selectedForecasts, timeAnchor, windowHours)
    return summary ? { ...summary, unit: unitOf('precipitation'), windowHours } : undefined
  }, [selectedForecasts, timeAnchor, unitOf])

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
          {selection && <div className="weather-precipitation-summary" aria-live="polite"><span>Next 24h precipitation</span><strong>{precipitationSummary?.amount == null ? 'No forecast' : `${precipitationSummary.amount.toFixed(1)} ${precipitationSummary.unit}`}</strong>{precipitationSummary?.volume != null && <small>{Math.round(precipitationSummary.volume).toLocaleString()} m³ over area</small>}{precipitationSummary != null && precipitationSummary.coveredHours > 0 && precipitationSummary.coveredHours < precipitationSummary.windowHours && <small>covers {precipitationSummary.coveredHours} of {precipitationSummary.windowHours} h</small>}</div>}
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
      <thead><tr><th aria-label="Type" /><th>Date &amp; time</th><th>Rainfall</th><th>Temperature</th>{visibleVariables.map(variableId => <th key={variableId}>{VARIABLE_LABELS[variableId] ?? variableId}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={`${row.kind}-${row.timestamp}`}>
        <td><span className={`weather-row-kind ${row.kind}`} role="img" title={row.kind === 'observation' ? 'Observation' : 'Forecast'} aria-label={row.kind === 'observation' ? 'Observation' : 'Forecast'}>{row.kind === 'observation' ? <Eye size={14} aria-hidden="true" /> : <CloudSun size={14} aria-hidden="true" />}</span></td>
        <td><time dateTime={row.timestamp}>{new Date(row.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time></td>
        <td><WeatherValue value={row.values.find(value => value.variableId === 'precipitation')} /></td>
        <td><WeatherValue value={row.values.find(value => value.variableId === 'temperature')} /></td>
        {visibleVariables.map(variableId => <td key={variableId}><WeatherValue value={row.values.find(value => value.variableId === variableId)} /></td>)}
      </tr>)}{!rows.length && <tr><td className="weather-table-empty" colSpan={4 + visibleVariables.length}>No observations or forecast values in this window.</td></tr>}</tbody>
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
