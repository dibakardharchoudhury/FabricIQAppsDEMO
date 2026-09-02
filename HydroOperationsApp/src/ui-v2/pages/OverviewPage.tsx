import { Activity, AlertTriangle, Factory, Gauge, MapPin, Radio, Wrench } from 'lucide-react'
import { FacilityContext } from '../components/FacilityContext'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function OverviewPage() {
  const data = useHydroOperationsData()
  const facility = data.selectedFacility
  const telemetryTone = data.telemetryStatus === 'live' ? 'good' : data.telemetryStatus === 'delayed' ? 'warn' : data.telemetryStatus === 'stale' ? 'bad' : 'muted'
  const qualityTone = data.counts.qualityIssues ? 'warn' : data.counts.liveSignals ? 'good' : 'muted'

  return <div className="v2-overview">
    {data.notice && <div className="v2-notice"><AlertTriangle size={15} /><span>{data.notice}</span></div>}

    <section className="v2-facility-banner">
      <div>
        <span className="v2-eyebrow">Facility Overview</span>
        <h1>{facility?.facility_name ?? 'Hydropower operations'}</h1>
        <p>{facility ? `${facility.facility_id} · ${facility.type ?? 'Facility'} · ${facility.country ?? 'Location unavailable'}` : 'STID metadata is not connected yet.'}</p>
      </div>
    </section>

    <FacilityContext />

    <section className="v2-summary-grid" aria-label="Facility summary">
      <SummaryCard icon={MapPin} label="Facilities" value={data.stidState === 'connected' ? data.counts.facilities : '—'} detail="Lakehouse STID" />
      <SummaryCard icon={Factory} label="Assets" value={data.stidState === 'connected' ? data.counts.assets : '—'} detail="Selected facility" />
      <SummaryCard icon={Gauge} label="Instruments" value={data.stidState === 'connected' ? data.counts.instruments : '—'} detail="Mapped OPC UA nodes" />
      <SummaryCard icon={Activity} label="Live signals" value={data.counts.liveSignals || '—'} detail="Eventhouse · 24h" />
      <SummaryCard icon={Wrench} label="Open work orders" value={data.operationsState === 'connected' ? data.counts.openWorkOrders : '—'} detail="Rayfin SQL" />
    </section>

    <section className="v2-health-panel">
      <div className="v2-panel-headline">
        <span className="v2-eyebrow">Operational Health</span>
        <h2>{data.telemetryStatus === 'live' && !data.counts.qualityIssues ? 'Operational data is healthy' : 'Operational data needs attention'}</h2>
      </div>
      <div className="v2-health-grid">
        <HealthItem tone={telemetryTone} icon={Radio} label="Telemetry freshness" value={data.telemetryStatusLabel} detail={data.telemetryAgeLabel ? `Median event age ${data.telemetryAgeLabel}` : 'No recent telemetry loaded'} />
        <HealthItem tone={qualityTone} icon={AlertTriangle} label="BAD / UNCERTAIN quality" value={String(data.counts.qualityIssues)} detail={`${data.counts.liveSignals || 0} signal${data.counts.liveSignals === 1 ? '' : 's'} in selected facility`} />
        <HealthItem tone={data.counts.openWorkOrders ? 'warn' : data.operationsState === 'connected' ? 'good' : 'muted'} icon={Wrench} label="Open maintenance work" value={data.operationsState === 'connected' ? String(data.counts.openWorkOrders) : 'Not connected'} detail="Work orders scoped to this facility" />
      </div>
    </section>
  </div>
}

function SummaryCard({ icon: Icon, label, value, detail }: { icon: typeof MapPin; label: string; value: string | number; detail: string }) {
  return <article className="v2-summary-card">
    <Icon size={18} />
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{detail}</small>
  </article>
}

function HealthItem({ tone, icon: Icon, label, value, detail }: { tone: 'good' | 'warn' | 'bad' | 'muted'; icon: typeof Radio; label: string; value: string; detail: string }) {
  return <article className={`v2-health-item ${tone}`}>
    <span className="v2-health-icon"><Icon size={17} /></span>
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  </article>
}