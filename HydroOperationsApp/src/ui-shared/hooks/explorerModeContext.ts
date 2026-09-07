import { createContext } from 'react'

export type ExplorerMode = 'filter' | 'tree' | 'dashboard'
export type ExplorerTabId = 'digital-twin' | 'telemetry'
export type ExplorerModes = {
  'digital-twin': Exclude<ExplorerMode, 'dashboard'>
  telemetry: ExplorerMode
}

export type ExplorerModeContextValue = {
  modes: ExplorerModes
  setMode: <TabId extends ExplorerTabId>(tabId: TabId, mode: ExplorerModes[TabId]) => void
}

export const ExplorerModeContext = createContext<ExplorerModeContextValue | null>(null)