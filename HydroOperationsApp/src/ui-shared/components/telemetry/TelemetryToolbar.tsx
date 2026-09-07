import type { ReactNode } from 'react'

export function TelemetryToolbar({ filters }: { filters?: ReactNode }) {
  return filters ? <section className="v2-telemetry-toolbar">{filters}</section> : null
}
