import { useCallback, useContext } from 'react'
import { ExplorerModeContext, type ExplorerModes, type ExplorerTabId } from './explorerModeContext'

export type { ExplorerMode } from './explorerModeContext'

export function useExplorerMode<TabId extends ExplorerTabId>(tabId: TabId) {
  const context = useContext(ExplorerModeContext)
  if (!context) throw new Error('useExplorerMode must be used within ExplorerModeProvider')
  const setMode = useCallback((mode: ExplorerModes[TabId]) => context.setMode(tabId, mode), [context, tabId])
  return { mode: context.modes[tabId], setMode }
}