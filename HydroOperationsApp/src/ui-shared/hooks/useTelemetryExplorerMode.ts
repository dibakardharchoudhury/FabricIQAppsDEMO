import { useCallback, useMemo, useState } from 'react'

export type TelemetryExplorerMode = 'filter' | 'tree' | 'dashboard'

const MODE_STORAGE_KEY = 'hydro.telemetry.explorer-mode.v1'
const MODES: readonly TelemetryExplorerMode[] = ['filter', 'tree', 'dashboard']

function readMode(): TelemetryExplorerMode {
  try {
    const stored = localStorage.getItem(MODE_STORAGE_KEY) as TelemetryExplorerMode | null
    return stored && MODES.includes(stored) ? stored : 'filter'
  }
  catch { return 'filter' }
}

export function useTelemetryExplorerMode() {
  const [mode, setModeState] = useState<TelemetryExplorerMode>(readMode)

  const setMode = useCallback((next: TelemetryExplorerMode) => {
    setModeState(next)
    try { localStorage.setItem(MODE_STORAGE_KEY, next) }
    catch { /* storage unavailable */ }
  }, [])

  return { mode, setMode }
}

/** Expanded-node ids: the path to the active signal opens by default, user toggles override it. */
export function useTreeExpansion(revealPath: string[]) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>({})
  const revealed = useMemo(() => new Set(revealPath), [revealPath])

  const isExpanded = useCallback((id: string) => overrides[id] ?? revealed.has(id), [overrides, revealed])
  const toggle = useCallback((id: string) => setOverrides(current => ({ ...current, [id]: !(current[id] ?? revealed.has(id)) })), [revealed])

  return { isExpanded, toggle }
}
