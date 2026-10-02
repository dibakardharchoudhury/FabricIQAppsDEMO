import { Activity, Bot, CloudSun, Factory, Gauge, Network, Settings, Wrench } from 'lucide-react'
import type { ComponentType, LazyExoticComponent } from 'react'
import {
  LazyAdministrationPage, LazyCopilotPage, LazyDigitalTwinPage, LazyKnowledgeGraphPage,
  LazyMaintenancePage, LazyOverviewPage, LazyTelemetryPage, LazyWeatherPage,
} from '../ui-shared/pages/lazyPages'

export type V1TabId = 'overview' | 'telemetry' | 'weather' | 'digital-twin' | 'knowledge-graph' | 'copilot' | 'maintenance' | 'administration'

export type V1Tab = {
  id: V1TabId
  label: string
  title: string
  icon: typeof Gauge
  Page: LazyExoticComponent<ComponentType>
}

export const V1_TABS: V1Tab[] = [
  { id: 'overview', label: 'Overview', title: 'Overview', icon: Gauge, Page: LazyOverviewPage },
  { id: 'telemetry', label: 'Real Time Telemetry', title: 'Real Time Telemetry', icon: Activity, Page: LazyTelemetryPage },
  { id: 'weather', label: 'Weather', title: 'Weather', icon: CloudSun, Page: LazyWeatherPage },
  { id: 'digital-twin', label: 'Digital Twin', title: 'Digital Twin', icon: Factory, Page: LazyDigitalTwinPage },
  { id: 'knowledge-graph', label: 'Knowledge Graph', title: 'Knowledge Graph', icon: Network, Page: LazyKnowledgeGraphPage },
  { id: 'copilot', label: 'Hydro Intelligence', title: 'Hydro Intelligence', icon: Bot, Page: LazyCopilotPage },
  { id: 'maintenance', label: 'Work Orders & Maintenance', title: 'Work Orders & Maintenance', icon: Wrench, Page: LazyMaintenancePage },
  { id: 'administration', label: 'Administration', title: 'Administration', icon: Settings, Page: LazyAdministrationPage },
]

export const DEFAULT_V1_TAB = V1_TABS[0]

export function resolveV1Tab(value: string | null): V1Tab {
  return V1_TABS.find(tab => tab.id === value) ?? DEFAULT_V1_TAB
}