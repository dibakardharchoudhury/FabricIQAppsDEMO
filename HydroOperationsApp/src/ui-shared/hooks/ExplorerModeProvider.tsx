import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { ExplorerModeContext, type ExplorerMode, type ExplorerTabId } from './explorerModeContext'

const STORAGE_KEYS: Record<ExplorerTabId, string> = {
  'digital-twin': 'hydro.digital-twin.explorer-mode.v1',
  telemetry: 'hydro.telemetry.explorer-mode.v1',
}

function readMode(tabId: ExplorerTabId): ExplorerMode {
  try { return localStorage.getItem(STORAGE_KEYS[tabId]) === 'tree' ? 'tree' : 'filter' }
  catch { return 'filter' }
}

export function ExplorerModeProvider({ children }: { children: ReactNode }) {
  const [modes, setModes] = useState<Record<ExplorerTabId, ExplorerMode>>(() => ({
    'digital-twin': readMode('digital-twin'),
    telemetry: readMode('telemetry'),
  }))

  const setMode = useCallback((tabId: ExplorerTabId, mode: ExplorerMode) => {
    setModes(current => ({ ...current, [tabId]: mode }))
    try { localStorage.setItem(STORAGE_KEYS[tabId], mode) }
    catch { /* storage unavailable */ }
  }, [])

  const value = useMemo(() => ({ modes, setMode }), [modes, setMode])
  return <ExplorerModeContext.Provider value={value}>{children}</ExplorerModeContext.Provider>
}