import { Activity, AlertTriangle, Radio } from 'lucide-react'
import { TelemetryStatusCard, type Tone } from './TelemetryCards'

const freshnessTone = (status: string): Tone => status === 'live' ? 'good' : status === 'delayed' ? 'warn' : status === 'stale' ? 'bad' : 'muted'

export type TelemetryStatusSummary = {
  telemetryStatus: string
  telemetryStatusLabel: string
  telemetryAgeLabel: string
  liveSignalCount: number
  assetLabel: string
  qualityIssueCount: number
}

/** Bare cards, so tree mode can put them in the same row as the selected-signal cards. */
export function TelemetryStatusCards({ telemetryStatus, telemetryStatusLabel, telemetryAgeLabel, liveSignalCount, assetLabel, qualityIssueCount }: TelemetryStatusSummary) {
  return <>
    <TelemetryStatusCard
      icon={Radio}
      label="Telemetry freshness"
      value={telemetryStatusLabel}
      detail={telemetryAgeLabel ? `Median event age ${telemetryAgeLabel}` : 'No telemetry rows loaded'}
      tone={freshnessTone(telemetryStatus)}
    />
    <TelemetryStatusCard
      icon={Activity}
      label="Live signals"
      value={String(liveSignalCount || '-')}
      detail={assetLabel}
      tone={liveSignalCount ? 'good' : 'muted'}
    />
    <TelemetryStatusCard
      icon={AlertTriangle}
      label="Quality issues"
      value={String(qualityIssueCount)}
      detail="BAD / UNCERTAIN latest quality"
      tone={qualityIssueCount ? 'warn' : liveSignalCount ? 'good' : 'muted'}
    />
  </>
}

export function TelemetryStatusBar(props: TelemetryStatusSummary) {
  return <section className="v2-telemetry-status"><TelemetryStatusCards {...props} /></section>
}
