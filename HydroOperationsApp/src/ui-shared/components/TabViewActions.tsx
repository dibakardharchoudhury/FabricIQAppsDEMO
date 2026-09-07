import type { ComponentType } from 'react'
import { DigitalTwinViewToggle } from './digitalTwin/DigitalTwinViewToggle'
import { useDigitalTwinExplorerMode } from '../hooks/useDigitalTwinExplorerMode'

type TabViewActionProps = { tabId: string }

function DigitalTwinActions() {
  const { mode, setMode } = useDigitalTwinExplorerMode()
  return <DigitalTwinViewToggle mode={mode} onModeChange={setMode} />
}

const ACTIONS: Partial<Record<string, ComponentType>> = {
  'digital-twin': DigitalTwinActions,
}

export function TabViewActions({ tabId }: TabViewActionProps) {
  const Actions = ACTIONS[tabId]
  return Actions ? <div className="v2-tab-view-actions"><Actions /></div> : null
}