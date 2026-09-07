import { createContext } from 'react'

export type ExplorerMode = 'filter' | 'tree'
export type ExplorerTabId = 'digital-twin' | 'telemetry'

export type ExplorerModeContextValue = {
  modes: Record<ExplorerTabId, ExplorerMode>
  setMode: (tabId: ExplorerTabId, mode: ExplorerMode) => void
}

export const ExplorerModeContext = createContext<ExplorerModeContextValue | null>(null)