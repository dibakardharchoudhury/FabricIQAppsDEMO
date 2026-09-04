import type { ReactNode } from 'react'
import type { Instrument, TelemetryHistoryRange } from '../../../services/fabric'
import type { TelemetryHistoryResult } from '../../hooks/useTelemetryHistory'
import { TelemetryChartPanel } from './TelemetryChartPanel'
import { TelemetryEmptyPanel } from './TelemetryCards'

export function TelemetryDetail({ signal, signals, summary, history, range, children }: {
  signal?: Instrument
  signals: Instrument[]
  summary: ReactNode
  history: TelemetryHistoryResult
  range: TelemetryHistoryRange
  children?: ReactNode
}) {
  if (!signals.length) return <TelemetryEmptyPanel title="No instruments for this asset" text="The selected asset has no STID instrument records." />
  return <div className="v2-telemetry-detail">
    {summary}
    <TelemetryChartPanel signal={signal} history={history} range={range} />
    {children}
  </div>
}
