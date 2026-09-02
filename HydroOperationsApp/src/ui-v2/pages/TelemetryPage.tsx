import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Clock, Gauge, Radio } from 'lucide-react'
import { queryTelemetryHistory, type TelemetryHistoryPoint, type TelemetryHistoryRange } from '../../services/fabric'
import { ageLabel, freshnessOf } from '../../twin'
import { FacilityContext } from '../components/FacilityContext'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

const ranges: TelemetryHistoryRange[] = ['1h', '6h', '24h']
const issueQualities = new Set(['bad', 'uncertain'])

export function TelemetryPage() {
  const data = useHydroOperationsData()
  const [history, setHistory] = useState<TelemetryHistoryPoint[]>([])
  const [historyState, setHistoryState] = useState<'idle' | 'loading' | 'ready' | 'empty' | 'unavailable' | 'error'>('idle')
  const [historyError, setHistoryError] = useState<string>()

  const selectedAssetId = data.telemetryExplorerSelection.assetId
  const selectedSignalId = data.telemetryExplorerSelection.signalId
  const range = data.telemetryExplorerSelection.range
  const asset = data.facilityEquipment.find(item => item.equipment_id === selectedAssetId) ?? data.facilityEquipment[0]
  const signals = useMemo(
    () => data.facilityInstruments.filter(item => item.equipment_id === asset?.equipment_id),
    [data.facilityInstruments, asset],
  )
  const signal = signals.find(item => item.instrument_id === selectedSignalId) ?? signals[0]
  const latestByNode = useMemo(() => new Map(data.facilityTelemetry.map(item => [item.opcuaNodeId, item])), [data.facilityTelemetry])
  const latest = signal ? latestByNode.get(signal.opcua_node_id) : undefined
  const qualityIssueCount = data.facilityTelemetry.filter(item => issueQualities.has((item.quality ?? '').toLowerCase())).length

  useEffect(() => {
    if (!signal) return
    let cancelled = false
    const load = async () => {
      setHistoryState('loading')
      setHistoryError(undefined)
      try {
        const rows = await queryTelemetryHistory(signal.opcua_node_id, range)
        if (cancelled) return
        if (rows === null) { setHistory([]); setHistoryState('unavailable'); return }
        setHistory(rows)
        setHistoryState(rows.length ? 'ready' : 'empty')
      } catch (error) {
        if (cancelled) return
        setHistory([])
        setHistoryState('error')
        setHistoryError(error instanceof Error ? error.message : 'Historical telemetry query failed.')
      }
    }
    void load()
    return () => { cancelled = true }
  }, [signal, range])

  return <div className="v2-domain-page">
    <FacilityContext />

    <section className="v2-telemetry-status">
      <TelemetryStatusCard icon={Radio} label="Telemetry freshness" value={data.telemetryStatusLabel} detail={data.telemetryAgeLabel ? `Median event age ${data.telemetryAgeLabel}` : 'No telemetry rows loaded'} tone={data.telemetryStatus === 'live' ? 'good' : data.telemetryStatus === 'delayed' ? 'warn' : data.telemetryStatus === 'stale' ? 'bad' : 'muted'} />
      <TelemetryStatusCard icon={Activity} label="Live signals" value={String(data.counts.liveSignals || '-')} detail="Selected facility" tone={data.counts.liveSignals ? 'good' : 'muted'} />
      <TelemetryStatusCard icon={AlertTriangle} label="Quality issues" value={String(qualityIssueCount)} detail="BAD / UNCERTAIN latest quality" tone={qualityIssueCount ? 'warn' : data.counts.liveSignals ? 'good' : 'muted'} />
    </section>

    {data.stidState !== 'connected' ? <EmptyPanel title="STID not connected" text="Use Administration to connect STID before exploring telemetry by asset and signal." />
      : data.telemetryState !== 'connected' ? <EmptyPanel title="Telemetry not connected" text="Use Administration to connect telemetry before viewing latest readings and history." />
        : !data.facilityEquipment.length ? <EmptyPanel title="No assets for this facility" text="The selected facility has no STID equipment records." />
          : <TelemetryExplorer
              assets={data.facilityEquipment}
              assetId={asset?.equipment_id}
              onAssetChange={id => data.actions.updateTelemetryExplorerSelection({ assetId: id, signalId: undefined })}
              signals={signals}
              signalId={signal?.instrument_id}
              onSignalChange={signalId => data.actions.updateTelemetryExplorerSelection({ signalId })}
              range={range}
              onRangeChange={range => data.actions.updateTelemetryExplorerSelection({ range })}
              latest={latest}
              latestByNode={latestByNode}
              signal={signal}
              history={history}
              historyState={historyState}
              historyError={historyError}
            />}
  </div>
}

function TelemetryExplorer({ assets, assetId, onAssetChange, signals, signalId, onSignalChange, range, onRangeChange, latest, latestByNode, signal, history, historyState, historyError }: {
  assets: Array<{ equipment_id: string; tag?: string }>
  assetId?: string
  onAssetChange: (id: string) => void
  signals: Array<{ instrument_id: string; tag?: string; unit?: string; opcua_node_id: string }>
  signalId?: string
  onSignalChange: (id: string) => void
  range: TelemetryHistoryRange
  onRangeChange: (range: TelemetryHistoryRange) => void
  latest?: { value: number; quality: string; eventTime: string }
  latestByNode: Map<string, { value: number; quality: string; eventTime: string }>
  signal?: { instrument_id: string; tag?: string; unit?: string; opcua_node_id: string }
  history: TelemetryHistoryPoint[]
  historyState: 'idle' | 'loading' | 'ready' | 'empty' | 'unavailable' | 'error'
  historyError?: string
}) {
  return <section className="v2-telemetry-explorer">
    <div className="v2-selector-row">
      <label><span>Asset</span><select value={assetId ?? ''} onChange={event => onAssetChange(event.target.value)}>{assets.map(item => <option key={item.equipment_id} value={item.equipment_id}>{item.tag ?? item.equipment_id}</option>)}</select></label>
      <label><span>Signal</span><select value={signalId ?? ''} onChange={event => onSignalChange(event.target.value)} disabled={!signals.length}>{signals.map(item => <option key={item.instrument_id} value={item.instrument_id}>{item.tag ?? item.instrument_id}</option>)}</select></label>
      <div className="v2-range-control" role="group" aria-label="Historical telemetry range">{ranges.map(item => <button key={item} type="button" className={range === item ? 'active' : ''} onClick={() => onRangeChange(item)}>{item}</button>)}</div>
    </div>

    {!signals.length ? <EmptyPanel title="No instruments for this asset" text="The selected asset has no STID instrument records." />
      : <>
        <section className="v2-current-signal">
          <div><span className="v2-eyebrow">Selected Signal</span><h1>{signal?.tag ?? signal?.instrument_id}</h1><p>{signal?.opcua_node_id}</p></div>
          <SignalMetric icon={Gauge} label="Current value" value={latest ? latest.value.toFixed(2) : '-'} detail={signal?.unit ?? 'No unit'} />
          <SignalMetric icon={Radio} label="Current quality" value={latest?.quality ?? 'No reading'} detail={latest ? ageLabel(latest.eventTime) : 'No latest event'} tone={latest && issueQualities.has(latest.quality.toLowerCase()) ? 'warn' : latest ? 'good' : 'muted'} />
          <SignalMetric icon={Clock} label="Last update" value={latest ? ageLabel(latest.eventTime) : '-'} detail={latest?.eventTime ? new Date(latest.eventTime).toLocaleString() : 'No latest event'} />
        </section>

        <section className="v2-chart-panel">
          <div className="v2-panel-headline"><span className="v2-eyebrow">Time Series</span><h2>{signal?.tag ?? 'Signal'} value{signal?.unit ? ` (${signal.unit})` : ''}</h2></div>
          {historyState === 'loading' ? <div className="v2-chart-empty">Loading historical telemetry...</div>
            : historyState === 'unavailable' ? <div className="v2-chart-empty">Historical telemetry is not connected. Use Administration to connect telemetry.</div>
              : historyState === 'error' ? <div className="v2-chart-empty">{historyError}</div>
                : historyState === 'empty' ? <div className="v2-chart-empty">No historical points in the selected range.</div>
                  : <TelemetryChart points={history} unit={signal?.unit} range={range} />}
        </section>

        <section className="v2-signal-table-panel">
          <div className="v2-panel-headline"><span className="v2-eyebrow">Asset Signals</span><h2>Signals for selected asset</h2></div>
          <div className="v2-signal-table">
            <div className="v2-signal-row head"><span>Signal</span><span>Latest value</span><span>Quality</span><span>Updated</span></div>
            {signals.map(item => {
              const reading = latestByNode.get(item.opcua_node_id)
              const active = item.instrument_id === signal?.instrument_id
              const quality = reading?.quality?.toLowerCase() ?? ''
              return <button key={item.instrument_id} type="button" className={active ? 'v2-signal-row active' : 'v2-signal-row'} onClick={() => onSignalChange(item.instrument_id)}>
                <span><strong>{item.tag ?? item.instrument_id}</strong><small>{item.opcua_node_id}</small></span>
                <span>{reading ? `${reading.value.toFixed(2)}${item.unit ? ` ${item.unit}` : ''}` : '-'}</span>
                <span className={quality}>{reading?.quality ?? '-'}</span>
                <span className={freshnessOf(reading?.eventTime)}>{reading ? ageLabel(reading.eventTime) : 'No event'}</span>
              </button>
            })}
          </div>
        </section>
      </>}
  </section>
}

function TelemetryStatusCard({ icon: Icon, label, value, detail, tone }: { icon: typeof Radio; label: string; value: string; detail: string; tone: 'good' | 'warn' | 'bad' | 'muted' }) {
  return <article className={`v2-health-item ${tone}`}><span className="v2-health-icon"><Icon size={17} /></span><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>
}

function SignalMetric({ icon: Icon, label, value, detail, tone = 'muted' }: { icon: typeof Gauge; label: string; value: string; detail: string; tone?: 'good' | 'warn' | 'muted' }) {
  return <article className={`v2-signal-metric ${tone}`}><span><Icon size={16} /></span><div><small>{label}</small><strong>{value}</strong><em>{detail}</em></div></article>
}

function EmptyPanel({ title, text }: { title: string; text: string }) {
  return <section className="v2-placeholder-card"><span className="v2-eyebrow">Real Time Telemetry</span><h1>{title}</h1><p className="v2-empty-copy">{text}</p></section>
}

function TelemetryChart({ points, unit, range }: { points: TelemetryHistoryPoint[]; unit?: string; range: TelemetryHistoryRange }) {
  const width = 860
  const height = 280
  const pad = 34
  const ordered = [...points]
    .map(point => ({ ...point, timeMs: Date.parse(point.eventTime) }))
    .filter(point => !Number.isNaN(point.timeMs))
    .sort((a, b) => a.timeMs - b.timeMs)
  const values = ordered.map(point => point.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = Math.max(1, max - min)
  const firstMs = ordered[0]?.timeMs ?? 0
  const lastMs = ordered[ordered.length - 1]?.timeMs ?? firstMs
  const timeSpan = Math.max(1, lastMs - firstMs)
  const x = (timeMs: number) => ordered.length <= 1 ? width / 2 : pad + ((timeMs - firstMs) / timeSpan) * (width - pad * 2)
  const y = (value: number) => height - pad - ((value - min) / span) * (height - pad * 2)
  const line = ordered.map(point => `${x(point.timeMs)},${y(point.value)}`).join(' ')
  const badPoints = ordered.filter(point => point.quality.toLowerCase() === 'bad')
  const ticks = timeTicks(ordered)
  const availableHistory = formatHistoryDuration(firstMs, lastMs)
  return <div className="v2-chart-wrap">
    <div className="v2-chart-coverage"><span>Selected range: {range}</span><span>Available history: {availableHistory}</span><span>{ordered.length} aggregated points</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} className="v2-telemetry-chart" role="img" aria-label={`Telemetry history chart${unit ? ` in ${unit}` : ''}`}>
      <line className="axis" x1={pad} y1={pad} x2={pad} y2={height - pad} />
      <line className="axis" x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} />
      <text x={pad} y={pad - 10}>{max.toFixed(1)}{unit ? ` ${unit}` : ''}</text>
      <text x={pad} y={height - 8}>{min.toFixed(1)}{unit ? ` ${unit}` : ''}</text>
      {ticks.map(tick => <g key={tick.timeMs} className="tick"><line x1={x(tick.timeMs)} y1={height - pad} x2={x(tick.timeMs)} y2={height - pad + 5} /><text x={x(tick.timeMs)} y={height - 14} textAnchor="middle">{tick.label}</text></g>)}
      <polyline className="series" points={line} />
      {badPoints.map(point => <circle key={`bad-${point.eventTime}`} className="issue bad" cx={x(point.timeMs)} cy={y(point.value)} r={4.2}><title>BAD quality issue - {new Date(point.eventTime).toLocaleString()}</title></circle>)}
    </svg>
    <div className="v2-chart-foot"><span>{ordered.length} aggregated points</span><span>{badPoints.length} BAD interval{badPoints.length === 1 ? '' : 's'}</span></div>
    <div className="v2-chart-legend" aria-label="Telemetry quality legend"><span><i className="line" />Teal line = telemetry value</span><span><i className="bad" />Red point = interval contained BAD reading</span></div>
  </div>
}

function timeTicks(points: Array<TelemetryHistoryPoint & { timeMs: number }>) {
  if (!points.length) return []
  const count = Math.min(4, points.length)
  const indexes = Array.from({ length: count }, (_, index) => Math.round(index * (points.length - 1) / Math.max(1, count - 1)))
  const unique = [...new Set(indexes)]
  const crossesDate = new Date(points[0].timeMs).toDateString() !== new Date(points[points.length - 1].timeMs).toDateString()
  return unique.map(index => {
    const point = points[index]
    const date = new Date(point.timeMs)
    return {
      timeMs: point.timeMs,
      label: date.toLocaleString([], crossesDate ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' } : { hour: '2-digit', minute: '2-digit' }),
    }
  })
}

function formatHistoryDuration(firstMs: number, lastMs: number) {
  if (!firstMs || !lastMs || lastMs <= firstMs) return 'single point'
  const minutes = Math.round((lastMs - firstMs) / 60000)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`
}