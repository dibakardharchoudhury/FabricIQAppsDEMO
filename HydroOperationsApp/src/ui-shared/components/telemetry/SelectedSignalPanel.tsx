import { Clock, Gauge, Radio } from 'lucide-react'
import type { Instrument, TelemetryReading } from '../../../services/fabric'
import { ageLabel } from '../../../twin'
import { SignalMetric } from './TelemetryCards'

const issueQualities = new Set(['bad', 'uncertain'])

export type SelectedSignalProps = { signal?: Instrument; latest?: TelemetryReading }

export function SelectedSignalCards({ signal, latest }: SelectedSignalProps) {
  return <>
    <div className="v2-signal-identity">
      <span className="v2-eyebrow">Selected Signal</span>
      <h1>{signal?.tag ?? signal?.instrument_id ?? '-'}</h1>
      <p>{signal?.opcua_node_id}</p>
    </div>
    <SignalMetric icon={Gauge} label="Current value" value={latest ? latest.value.toFixed(2) : '-'} detail={signal?.unit ?? 'No unit'} />
    <SignalMetric
      icon={Radio}
      label="Current quality"
      value={latest?.quality ?? 'No reading'}
      detail={latest ? ageLabel(latest.eventTime) : 'No latest event'}
      tone={latest && issueQualities.has(latest.quality.toLowerCase()) ? 'warn' : latest ? 'good' : 'muted'}
    />
    <SignalMetric
      icon={Clock}
      label="Last update"
      value={latest ? ageLabel(latest.eventTime) : '-'}
      detail={latest?.eventTime ? new Date(latest.eventTime).toLocaleString() : 'No latest event'}
    />
  </>
}

export function SelectedSignalPanel(props: SelectedSignalProps) {
  return <section className="v2-current-signal"><SelectedSignalCards {...props} /></section>
}
