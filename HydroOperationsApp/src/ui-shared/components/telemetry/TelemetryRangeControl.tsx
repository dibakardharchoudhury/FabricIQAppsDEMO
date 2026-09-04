import type { TelemetryHistoryRange } from '../../../services/fabric'

const RANGES: TelemetryHistoryRange[] = ['1h', '6h', '24h']

export function TelemetryRangeControl({ range, onRangeChange }: { range: TelemetryHistoryRange; onRangeChange: (range: TelemetryHistoryRange) => void }) {
  return <div className="v2-range-control" role="group" aria-label="Historical telemetry range">
    {RANGES.map(item => <button key={item} type="button" className={range === item ? 'active' : ''} aria-pressed={range === item} onClick={() => onRangeChange(item)}>{item}</button>)}
  </div>
}
