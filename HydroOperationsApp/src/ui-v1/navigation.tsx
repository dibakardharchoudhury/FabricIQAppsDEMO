import { Activity, Bot, CloudSun, Factory, Gauge, Network, Settings, Wrench } from 'lucide-react'
import { lazy, type ComponentType } from 'react'

export type V1TabId = 'overview' | 'telemetry' | 'weather' | 'digital-twin' | 'knowledge-graph' | 'copilot' | 'maintenance' | 'administration'

export type V1Tab = {
  id: V1TabId
  label: string
  title: string
  icon: typeof Gauge
  Page: ComponentType
}

export const V1_TABS: V1Tab[] = [
  { id: 'overview', label: 'Overview', title: 'Overview', icon: Gauge, Page: lazy(() => import('./pages/OverviewPage').then(module => ({ default: module.OverviewPage }))) },
  { id: 'telemetry', label: 'Real Time Telemetry', title: 'Real Time Telemetry', icon: Activity, Page: lazy(() => import('./pages/TelemetryPage').then(module => ({ default: module.TelemetryPage }))) },
  { id: 'weather', label: 'Weather', title: 'Weather', icon: CloudSun, Page: lazy(() => import('./pages/WeatherPage').then(module => ({ default: module.WeatherPage }))) },
  { id: 'digital-twin', label: 'Digital Twin', title: 'Digital Twin', icon: Factory, Page: lazy(() => import('./pages/DigitalTwinPage').then(module => ({ default: module.DigitalTwinPage }))) },
  { id: 'knowledge-graph', label: 'Knowledge Graph', title: 'Knowledge Graph', icon: Network, Page: lazy(() => import('./pages/KnowledgeGraphPage').then(module => ({ default: module.KnowledgeGraphPage }))) },
  { id: 'copilot', label: 'Hydro Intelligence', title: 'Hydro Intelligence', icon: Bot, Page: lazy(() => import('./pages/CopilotPage').then(module => ({ default: module.CopilotPage }))) },
  { id: 'maintenance', label: 'Work Orders & Maintenance', title: 'Work Orders & Maintenance', icon: Wrench, Page: lazy(() => import('./pages/MaintenancePage').then(module => ({ default: module.MaintenancePage }))) },
  { id: 'administration', label: 'Administration', title: 'Administration', icon: Settings, Page: lazy(() => import('./pages/AdministrationPage').then(module => ({ default: module.AdministrationPage }))) },
]

export const DEFAULT_V1_TAB = V1_TABS[0]

export function resolveV1Tab(value: string | null): V1Tab {
  return V1_TABS.find(tab => tab.id === value) ?? DEFAULT_V1_TAB
}