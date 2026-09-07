import { useCallback, useState } from 'react'

export type DigitalTwinExplorerMode = 'filter' | 'tree'

const MODE_STORAGE_KEY = 'hydro.digital-twin.explorer-mode.v1'

function readMode(): DigitalTwinExplorerMode {
  try { return localStorage.getItem(MODE_STORAGE_KEY) === 'tree' ? 'tree' : 'filter' }
  catch { return 'filter' }
}

export function useDigitalTwinExplorerMode() {
  const [mode, setModeState] = useState<DigitalTwinExplorerMode>(readMode)

  const setMode = useCallback((next: DigitalTwinExplorerMode) => {
    setModeState(next)
    try { localStorage.setItem(MODE_STORAGE_KEY, next) }
    catch { /* storage unavailable */ }
  }, [])

  return { mode, setMode }
}