import { useExplorerMode, type ExplorerMode } from './useExplorerMode'

export type DigitalTwinExplorerMode = ExplorerMode

export function useDigitalTwinExplorerMode() {
  return useExplorerMode('digital-twin')
}