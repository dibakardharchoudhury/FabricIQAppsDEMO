import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { ExplorerModeContext, type ExplorerMode, type ExplorerModes, type ExplorerTabId } from './explorerModeContext'

const STORAGE_KEYS: Record<ExplorerTabId, string> = {
  'digital-twin': 'hydro.digital-twin.explorer-mode.v1',
  telemetry: 'hydro.telemetry.explorer-mode.v1',
}

function readMode(tabId: 'digital-twin'): ExplorerModes['digital-twin']
function readMode(tabId: 'telemetry'): ExplorerModes['telemetry']
function readMode(tabId: ExplorerTabId): ExplorerMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEYS[tabId])
    if (stored === 'tree' || (tabId === 'telemetry' && stored === 'dashboard')) return stored
    return 'filter'
  }
  catch { return 'filter' }
}

export function ExplorerModeProvider({ children }: { children: ReactNode }) {
  const [modes, setModes] = useState<ExplorerModes>(() => ({
    'digital-twin': readMode('digital-twin'),
    telemetry: readMode('telemetry'),
  }))

  const setMode = useCallback(<TabId extends ExplorerTabId>(tabId: TabId, mode: ExplorerModes[TabId]) => {
    setModes(current => ({ ...current, [tabId]: mode }))
    try { localStorage.setItem(STORAGE_KEYS[tabId], mode) }
    catch { /* storage unavailable */ }
  }, [])

  const value = useMemo(() => ({ modes, setMode }), [modes, setMode])
  return <ExplorerModeContext.Provider value={value}>{children}</ExplorerModeContext.Provider>
}