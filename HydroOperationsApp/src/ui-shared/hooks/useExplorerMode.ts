import { useCallback, useContext } from 'react'
import { ExplorerModeContext, type ExplorerMode, type ExplorerTabId } from './explorerModeContext'

export type { ExplorerMode } from './explorerModeContext'

export function useExplorerMode(tabId: ExplorerTabId) {
  const context = useContext(ExplorerModeContext)
  if (!context) throw new Error('useExplorerMode must be used within ExplorerModeProvider')
  const setMode = useCallback((mode: ExplorerMode) => context.setMode(tabId, mode), [context, tabId])
  return { mode: context.modes[tabId], setMode }
}