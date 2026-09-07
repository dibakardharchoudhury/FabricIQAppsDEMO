import { ListTree, SlidersHorizontal } from 'lucide-react'
import type { DigitalTwinExplorerMode } from '../../hooks/useDigitalTwinExplorerMode'

const OPTIONS: Array<{ mode: DigitalTwinExplorerMode; label: string; icon: typeof ListTree }> = [
  { mode: 'tree', label: 'Tree', icon: ListTree },
  { mode: 'filter', label: 'Filter', icon: SlidersHorizontal },
]

export function DigitalTwinViewToggle({ mode, onModeChange }: { mode: DigitalTwinExplorerMode; onModeChange: (mode: DigitalTwinExplorerMode) => void }) {
  return <div className="v2-view-toggle" role="group" aria-label="Digital Twin browsing mode">
    {OPTIONS.map(({ mode: option, label, icon: Icon }) => {
      const active = mode === option
      return <button key={option} type="button" className={active ? 'active' : ''} aria-pressed={active} onClick={() => onModeChange(option)}>
        <Icon size={14} /><span>{label}</span>
      </button>
    })}
  </div>
}