import { useCallback, useState } from 'react'

export type TelemetryExplorerMode = 'filter' | 'tree'

const MODE_STORAGE_KEY = 'hydro.telemetry.explorer-mode.v1'

function readMode(): TelemetryExplorerMode {
  try { return localStorage.getItem(MODE_STORAGE_KEY) === 'tree' ? 'tree' : 'filter' }
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
