import type { ComponentType } from 'react'
import { DigitalTwinViewToggle } from './digitalTwin/DigitalTwinViewToggle'
import { TelemetryRangeControl } from './telemetry/TelemetryRangeControl'
import { TelemetryViewToggle } from './telemetry/TelemetryViewToggle'
import { useDigitalTwinExplorerMode } from '../hooks/useDigitalTwinExplorerMode'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useTelemetryExplorerMode } from '../hooks/useTelemetryExplorerMode'

type TabViewActionProps = { tabId: string }

function DigitalTwinActions() {
  const { mode, setMode } = useDigitalTwinExplorerMode()
  return <DigitalTwinViewToggle mode={mode} onModeChange={setMode} />
}

function TelemetryActions() {
  const data = useHydroOperationsData()
  const { mode, setMode } = useTelemetryExplorerMode()
  const { range } = data.telemetryExplorerSelection
  return <>
    <TelemetryViewToggle mode={mode} onModeChange={setMode} />
    <TelemetryRangeControl range={range} onRangeChange={next => data.actions.updateTelemetryExplorerSelection({ range: next })} />
  </>
}

const ACTIONS: Partial<Record<string, ComponentType>> = {
  'digital-twin': DigitalTwinActions,
  telemetry: TelemetryActions,
}

export function TabViewActions({ tabId }: TabViewActionProps) {
  const Actions = ACTIONS[tabId]
  return Actions ? <div className="v2-tab-view-actions"><Actions /></div> : null
}