import type { Instrument, TelemetryReading } from '../../../services/fabric'
import { ageLabel, freshnessOf } from '../../../twin'

export function SignalTablePanel({ signals, selectedSignalId, readingOf, onSelectSignal }: {
  signals: Instrument[]
  selectedSignalId?: string
  readingOf: (opcuaNodeId: string) => TelemetryReading | undefined
  onSelectSignal: (signalId: string) => void
}) {
  return <section className="v2-signal-table-panel">
    <div className="v2-panel-headline"><span className="v2-eyebrow">Asset Signals</span><h2>Signals for selected asset</h2></div>
    <div className="v2-signal-table">
      <div className="v2-signal-row head"><span>Signal</span><span>Latest value</span><span>Quality</span><span>Updated</span></div>
      {signals.map(item => {
        const reading = readingOf(item.opcua_node_id)
        const active = item.instrument_id === selectedSignalId
        return <button key={item.instrument_id} type="button" className={active ? 'v2-signal-row active' : 'v2-signal-row'} onClick={() => onSelectSignal(item.instrument_id)}>
          <span><strong>{item.tag ?? item.instrument_id}</strong><small>{item.opcua_node_id}</small></span>
          <span>{reading ? `${reading.value.toFixed(2)}${item.unit ? ` ${item.unit}` : ''}` : '-'}</span>
          <span className={reading?.quality?.toLowerCase() ?? ''}>{reading?.quality ?? '-'}</span>
          <span className={freshnessOf(reading?.eventTime)}>{reading ? ageLabel(reading.eventTime) : 'No event'}</span>
        </button>
      })}
    </div>
  </section>
}
