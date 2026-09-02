import { Activity, Bot, Factory, Gauge, Settings, Wrench } from 'lucide-react'
import type { ComponentType } from 'react'
import { AdministrationPage } from './pages/AdministrationPage'
import { CopilotPage } from './pages/CopilotPage'
import { DigitalTwinPage } from './pages/DigitalTwinPage'
import { MaintenancePage } from './pages/MaintenancePage'
import { OverviewPage } from './pages/OverviewPage'
import { TelemetryPage } from './pages/TelemetryPage'

export type V2TabId = 'overview' | 'telemetry' | 'digital-twin' | 'copilot' | 'maintenance' | 'administration'

export type V2Tab = {
  id: V2TabId
  label: string
  title: string
  icon: typeof Gauge
  Page: ComponentType
}

export const V2_TABS: V2Tab[] = [
  { id: 'overview', label: 'Overview', title: 'Overview', icon: Gauge, Page: OverviewPage },
  { id: 'telemetry', label: 'Real Time Telemetry', title: 'Real Time Telemetry', icon: Activity, Page: TelemetryPage },
  { id: 'digital-twin', label: 'Digital Twin', title: 'Digital Twin', icon: Factory, Page: DigitalTwinPage },
  { id: 'copilot', label: 'Copilot', title: 'Copilot', icon: Bot, Page: CopilotPage },
  { id: 'maintenance', label: 'Work Orders & Maintenance', title: 'Work Orders & Maintenance', icon: Wrench, Page: MaintenancePage },
  { id: 'administration', label: 'Administration', title: 'Administration', icon: Settings, Page: AdministrationPage },
]

export const DEFAULT_V2_TAB = V2_TABS[0]

export function resolveV2Tab(value: string | null): V2Tab {
  return V2_TABS.find(tab => tab.id === value) ?? DEFAULT_V2_TAB
}