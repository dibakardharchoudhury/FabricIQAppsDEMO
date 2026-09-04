import type { ReactNode } from 'react'
import type { TelemetryHistoryRange } from '../../../services/fabric'
import type { TelemetryExplorerMode } from '../../hooks/useTelemetryExplorerMode'
import { TelemetryRangeControl } from './TelemetryRangeControl'
import { TelemetryViewToggle } from './TelemetryViewToggle'

export function TelemetryToolbar({ mode, onModeChange, range, onRangeChange, filters }: {
  mode: TelemetryExplorerMode
  onModeChange: (mode: TelemetryExplorerMode) => void
  range: TelemetryHistoryRange
  onRangeChange: (range: TelemetryHistoryRange) => void
  filters?: ReactNode
}) {
  return <section className="v2-telemetry-toolbar">
    <TelemetryViewToggle mode={mode} onModeChange={onModeChange} />
    {filters}
    {/* The embedded dashboard carries its own time-range parameter. */}
    {mode !== 'dashboard' && <TelemetryRangeControl range={range} onRangeChange={onRangeChange} />}
  </section>
}
