import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CloudSun, RefreshCw } from 'lucide-react'
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
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp))
}

export function WeatherPage() {
  const [weather, setWeather] = useState<WeatherData>()
  const [selection, setSelection] = useState<WeatherSelection>()
  const [rangeHours, setRangeHours] = useState(24)
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable' | 'error'>('loading')
  const [error, setError] = useState<string>()
  const [timeAnchor, setTimeAnchor] = useState(() => Date.now())
  const [forecastVendor, setForecastVendor] = useState('')
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set())

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
    ])].sort()
  }, [weather])
  const selectedVendor = forecastVendor || forecastVendors[0] || ''

  const timelines = useMemo(() => {
    if (!weather || !selection) return { observations: [], forecasts: [] }
    const now = timeAnchor
    const duration = rangeHours * 3_600_000
    if (selection.kind === 'location') {
      const observations = weather.observations.filter(item => item.location_id === selection.id && Date.parse(item.observed_at_utc) >= now - duration)
      const vendorForecasts = weather.forecasts.filter(item => item.location_id === selection.id && item.source_id === selectedVendor)
      const latestIssue = Math.max(0, ...vendorForecasts.map(item => Date.parse(item.reference_time_utc)))
      const forecasts = vendorForecasts.filter(item => Date.parse(item.reference_time_utc) === latestIssue && Date.parse(item.valid_time_utc) <= now + duration)
      return {
        observations: groupRows(observations, item => (item as WeatherObservation).observed_at_utc),
        forecasts: groupRows(forecasts, item => (item as WeatherForecast).valid_time_utc),
      }
    }
    const metrics = weather.areaMetrics.filter(item => item.area_id === selection.id)
    const observations = metrics.filter(item => item.data_kind === 'observation' && Date.parse(item.valid_time_utc) >= now - duration)
    const vendorForecasts = metrics.filter(item => item.data_kind === 'forecast' && item.source_id === selectedVendor)
    const latestIssue = Math.max(0, ...vendorForecasts.map(item => Date.parse(item.reference_time_utc ?? '')))
    const forecasts = vendorForecasts.filter(item => Date.parse(item.reference_time_utc ?? '') === latestIssue && Date.parse(item.valid_time_utc) <= now + duration)
    return {
      observations: groupRows(observations, item => (item as WeatherAreaMetric).valid_time_utc),
      forecasts: groupRows(forecasts, item => (item as WeatherAreaMetric).valid_time_utc),
    }
  }, [rangeHours, selectedVendor, selection, timeAnchor, weather])

  const toggleRow = (key: string) => setExpandedRows(current => {
    const next = new Set(current)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  return <div className="weather-page">
    <section className="weather-head">
      <div><span className="v2-eyebrow">Weather Operations</span><h1>Stations and local-area outlook</h1><p>Observed conditions and 72-hour Aurora forecasts at each asset and within its 20 km operating area.</p></div>
      <div className="weather-actions">
        <label>Location<select value={selection ? `${selection.kind}:${selection.id}` : ''} onChange={event => {
          const [kind, ...id] = event.target.value.split(':')
          if ((kind === 'location' || kind === 'area') && id.length) setSelection({ kind, id: id.join(':') })
        }}><option value="" disabled>Select</option>{weather?.locations.map(item => <option value={`location:${item.location_id}`} key={`location:${item.location_id}`}>{item.location_name}</option>)}{weather?.areas.map(item => <option value={`area:${item.area_id}`} key={`area:${item.area_id}`}>{item.area_name}</option>)}</select></label>
        <label>Vendor<select value={selectedVendor} onChange={event => setForecastVendor(event.target.value)} disabled={!forecastVendors.length}>{forecastVendors.map(vendor => <option value={vendor} key={vendor}>{vendor === 'aurora' ? 'Aurora' : vendor}</option>)}</select></label>
        <label>Window<select value={rangeHours} onChange={event => setRangeHours(Number(event.target.value))}>{RANGES.map(value => <option value={value} key={value}>{value} hours</option>)}</select></label>
        <button className="v2-icon-action" type="button" onClick={() => void load()} disabled={state === 'loading'} title="Refresh weather data" aria-label="Refresh weather data"><RefreshCw size={16} className={state === 'loading' ? 'spin' : undefined} /></button>
      </div>
    </section>

    {error && <div className="v2-notice" role="alert"><AlertTriangle size={15} /><span>{error}</span></div>}
    <section className="weather-workspace">
      <article className="weather-map-panel">
        {weather ? <WeatherMap locations={weather.locations} areas={weather.areas} selection={selection} onSelect={setSelection} /> : <div className="weather-map-empty" role="status" aria-live="polite">{state === 'loading' ? 'Loading weather map…' : <><span>Weather data needs Fabric GraphQL access.</span><button type="button" onClick={() => void load()}>Connect weather data</button></>}</div>}
        <div className="weather-legend"><span><i className="station" />Station</span><span><i className="area" />Area</span></div>
      </article>
      <aside className="weather-detail" aria-live="polite">
        <div className="weather-detail-head"><span className="weather-detail-icon"><CloudSun size={18} /></span><div><span>{selection?.kind === 'area' ? 'Weather area' : 'Weather station'}</span><h2>{selectedName ?? 'Select a station or area'}</h2></div></div>
        <TimelineSection title={`Observations · past ${rangeHours}h`} rows={timelines.observations} empty="No observations in this window." expandedRows={expandedRows} onToggle={toggleRow} />
        <TimelineSection title={`Forecast · next ${rangeHours}h`} rows={timelines.forecasts} empty="No forecast values in this window." expandedRows={expandedRows} onToggle={toggleRow} />
      </aside>
    </section>
  </div>
}

function TimelineSection({ title, rows, empty, expandedRows, onToggle }: { title: string; rows: TimelineRow[]; empty: string; expandedRows: Set<string>; onToggle: (key: string) => void }) {
  return <section className="weather-timeline-section"><div className="weather-timeline-title"><h3>{title}</h3><span>{rows.length}</span></div>
    <div className="weather-timeline">{rows.map(row => {
      const rowKey = `${title}-${row.timestamp}`
      const expanded = expandedRows.has(rowKey)
      const primary = row.values.filter(value => PRIMARY_VARIABLES.has(value.variableId))
      const secondary = row.values.filter(value => !PRIMARY_VARIABLES.has(value.variableId))
      return <article key={rowKey}>
        <time dateTime={row.timestamp}>{new Date(row.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time>
        <div className="weather-primary-values">{primary.map(value => <WeatherValue key={value.variableId} value={value} />)}</div>
        {expanded && <div className="weather-secondary-values">{secondary.map(value => <WeatherValue key={value.variableId} value={value} />)}</div>}
        {!!secondary.length && <button className="weather-more" type="button" aria-expanded={expanded} onClick={() => onToggle(rowKey)}>{expanded ? 'Less…' : 'More…'}</button>}
      </article>
    })}{!rows.length && <p className="weather-timeline-empty">{empty}</p>}</div>
  </section>
}

function WeatherValue({ value }: { value: TimelineValue }) {
  return <span><em>{VARIABLE_LABELS[value.variableId] ?? value.variableId}</em><strong>{formatValue(value)}</strong>{value.volume != null && <small>{Math.round(value.volume).toLocaleString()} m³</small>}</span>
}