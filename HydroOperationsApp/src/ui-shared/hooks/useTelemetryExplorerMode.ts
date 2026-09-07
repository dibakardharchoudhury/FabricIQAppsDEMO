import { useExplorerMode, type ExplorerMode } from './useExplorerMode'

export type TelemetryExplorerMode = ExplorerMode

export function useTelemetryExplorerMode() {
  return useExplorerMode('telemetry')
}
