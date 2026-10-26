import { lazy } from 'react'

export const LazyAdministrationPage = lazy(() => import('./AdministrationPage').then(module => ({ default: module.AdministrationPage })))
export const LazyCopilotPage = lazy(() => import('./CopilotPage').then(module => ({ default: module.CopilotPage })))
export const LazyDigitalTwinPage = lazy(() => import('./DigitalTwinPage').then(module => ({ default: module.DigitalTwinPage })))
export const LazyKnowledgeGraphPage = lazy(() => import('./KnowledgeGraphPage').then(module => ({ default: module.KnowledgeGraphPage })))
export const LazyMaintenancePage = lazy(() => import('./MaintenancePage').then(module => ({ default: module.MaintenancePage })))
export const LazyOverviewPage = lazy(() => import('./OverviewPage').then(module => ({ default: module.OverviewPage })))
export const LazyTelemetryPage = lazy(() => import('./TelemetryPage').then(module => ({ default: module.TelemetryPage })))
export const LazyWeatherPage = lazy(() => import('./WeatherPage').then(module => ({ default: module.WeatherPage })))
