import type { Instrument, TelemetryHistoryRange } from '../../../services/fabric'
import type { TelemetryHistoryResult } from '../../hooks/useTelemetryHistory'
import { TelemetryChart } from './TelemetryChart'

const MESSAGES: Partial<Record<TelemetryHistoryResult['state'], string>> = {
  idle: 'Select a signal to load its history.',
  loading: 'Loading historical telemetry...',
  unavailable: 'Historical telemetry is not connected. Use Administration to connect telemetry.',
  empty: 'No historical points in the selected range.',
}

export function TelemetryChartPanel({ signal, history, range }: { signal?: Instrument; history: TelemetryHistoryResult; range: TelemetryHistoryRange }) {
  const message = history.state === 'error' ? history.error ?? 'Historical telemetry query failed.' : MESSAGES[history.state]
  return <section className="v2-chart-panel">
    <div className="v2-panel-headline">
      <span className="v2-eyebrow">Time Series</span>
      <h2>{signal?.tag ?? 'Signal'} value{signal?.unit ? ` (${signal.unit})` : ''}</h2>
    </div>
    {message ? <div className="v2-chart-empty">{message}</div> : <TelemetryChart points={history.points} unit={signal?.unit} range={range} />}
  </section>
}
