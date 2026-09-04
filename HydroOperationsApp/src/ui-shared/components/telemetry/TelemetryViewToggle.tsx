import { ListTree, SlidersHorizontal } from 'lucide-react'
import type { TelemetryExplorerMode } from '../../hooks/useTelemetryExplorerMode'

const OPTIONS: Array<{ mode: TelemetryExplorerMode; label: string; icon: typeof ListTree }> = [
  { mode: 'tree', label: 'Tree', icon: ListTree },
  { mode: 'filter', label: 'Filter', icon: SlidersHorizontal },
]

export function TelemetryViewToggle({ mode, onModeChange }: { mode: TelemetryExplorerMode; onModeChange: (mode: TelemetryExplorerMode) => void }) {
  return <div className="v2-view-toggle" role="group" aria-label="Telemetry browsing mode">
    {OPTIONS.map(({ mode: option, label, icon: Icon }) => {
      const active = mode === option
      return <button key={option} type="button" className={active ? 'active' : ''} aria-pressed={active} onClick={() => onModeChange(option)}>
        <Icon size={14} /><span>{label}</span>
      </button>
    })}
  </div>
}
