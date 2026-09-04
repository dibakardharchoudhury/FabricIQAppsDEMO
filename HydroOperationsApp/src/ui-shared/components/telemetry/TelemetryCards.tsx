import type { LucideIcon } from 'lucide-react'

export type Tone = 'good' | 'warn' | 'bad' | 'muted'

export function TelemetryStatusCard({ icon: Icon, label, value, detail, tone }: { icon: LucideIcon; label: string; value: string; detail: string; tone: Tone }) {
  return <article className={`v2-health-item ${tone}`}><span className="v2-health-icon"><Icon size={17} /></span><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>
}

export function SignalMetric({ icon: Icon, label, value, detail, tone = 'muted' }: { icon: LucideIcon; label: string; value: string; detail: string; tone?: Tone }) {
  return <article className={`v2-signal-metric ${tone}`}><span><Icon size={16} /></span><div><small>{label}</small><strong>{value}</strong><em>{detail}</em></div></article>
}

export function TelemetryEmptyPanel({ title, text }: { title: string; text: string }) {
  return <section className="v2-placeholder-card"><span className="v2-eyebrow">Real Time Telemetry</span><h1>{title}</h1><p className="v2-empty-copy">{text}</p></section>
}
