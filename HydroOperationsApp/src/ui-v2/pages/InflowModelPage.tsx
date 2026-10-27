import { Activity, AlertTriangle, ArrowUpRight, CloudRain, Database, Gauge, Map, Waves } from 'lucide-react'
import './inflow-model.css'

const SOURCES = [
  {
    name: 'NVE HydAPI',
    role: 'Measured discharge and water level',
    detail: 'Bjørnegårdssvingen station 8.2.0 · hourly observations · 190.8 km² catchment',
    href: 'https://hydapi.nve.no/UserDocumentation/',
  },
  {
    name: 'MET Norway MEPS',
    role: 'Precipitation and temperature ensemble',
    detail: 'Lagged 2.5 km ensemble · 30 members',
    href: 'https://api.met.no/',
  },
  {
    name: 'MET Nordic analysis',
    role: 'Historical weather observations',
    detail: '1 km analysis product · archive from 2018',
    href: 'https://thredds.met.no/',
  },
  {
    name: 'Varsom',
    role: 'Official flood-warning context',
    detail: 'Use Varsom for current official warnings and advice.',
    href: 'https://www.varsom.no/',
  },
]

export function InflowModelPage() {
  return <div className="inflow-page">
    <header className="inflow-heading">
      <div>
        <span className="v2-eyebrow">Hydrology · Prototype</span>
        <h1>Lomma inflow model</h1>
        <p>Observed river conditions and ensemble-driven inflow forecasts for the Lomma catchment.</p>
      </div>
    </header>

    <section className="inflow-safety-notice" role="note" aria-label="Prototype limitation">
      <AlertTriangle size={19} aria-hidden="true" />
      <div>
        <strong>Prototype only — no operational forecast is available.</strong>
        <p>This view is not an official warning service or a life-safety tool. Check Varsom for current official information.</p>
      </div>
      <a href="https://www.varsom.no/" target="_blank" rel="noreferrer">Open Varsom <ArrowUpRight size={14} aria-hidden="true" /></a>
    </section>

    <section className="inflow-status-grid" aria-label="Inflow model status">
      <StatusCard icon={Gauge} title="Observed flow" state="Not connected" detail="No verified NVE observations are connected to this page." />
      <StatusCard icon={CloudRain} title="Weather ensemble" state="Not connected" detail="MET Norway precipitation and temperature inputs are not connected." />
      <StatusCard icon={Activity} title="Inflow forecast" state="Unavailable" detail="No basin-calibrated model output is available to display." />
    </section>

    <section className="inflow-main-grid">
      <article className="inflow-map-card">
        <div className="inflow-card-heading">
          <div><span className="inflow-kicker">Catchment view</span><h2>River and basin</h2></div>
          <span className="inflow-status-pill"><Map size={14} /> Geometry not provisioned</span>
        </div>
        <div className="inflow-map-empty" role="status">
          <Map size={28} aria-hidden="true" />
          <h3>No verified catchment map is available</h3>
          <p>Load authoritative river and catchment geometry before rendering map layers. No illustrative flow path or forecast value is shown.</p>
        </div>
      </article>

      <aside className="inflow-model-card">
        <div className="inflow-card-heading">
          <div><span className="inflow-kicker">Model readiness</span><h2>Inputs required</h2></div>
          <Waves size={19} aria-hidden="true" />
        </div>
        <ul className="inflow-readiness-list">
          <li><Database size={15} /><span><strong>HydAPI access</strong><small>Secure API key and verified station series</small></span><span className="inflow-readiness-state">Missing</span></li>
          <li><CloudRain size={15} /><span><strong>MEPS ensemble</strong><small>30-member precipitation and temperature feed</small></span><span className="inflow-readiness-state">Missing</span></li>
          <li><Activity size={15} /><span><strong>Calibrated model</strong><small>Verified basin parameters and held-out event checks</small></span><span className="inflow-readiness-state">Missing</span></li>
        </ul>
        <p className="inflow-model-note">Forecast probabilities remain unavailable until the source inputs, model calibration, and validation are established. Existing Hydro Operations telemetry and weather feeds are not substituted for Lomma basin data.</p>
      </aside>
    </section>

    <section className="inflow-method-section">
      <div className="inflow-section-title">
        <div><span className="inflow-kicker">Traceable inputs</span><h2>Data sources and method</h2></div>
        <p>Source integration and inflow calculations are not connected to this page.</p>
      </div>
      <div className="inflow-source-list">
        {SOURCES.map(source => <article className="inflow-source-row" key={source.name}>
          <div className="inflow-source-icon"><Database size={17} aria-hidden="true" /></div>
          <div className="inflow-source-copy">
            <h3>{source.name}</h3>
            <p>{source.role}</p>
            <small>{source.detail}</small>
          </div>
          <span className="inflow-source-state">Not connected</span>
          <a href={source.href} target="_blank" rel="noreferrer" aria-label={`Open ${source.name} documentation`}><ArrowUpRight size={16} aria-hidden="true" /></a>
        </article>)}
      </div>

      <div className="inflow-method-note">
        <div className="inflow-method-icon"><Waves size={19} aria-hidden="true" /></div>
        <div>
          <h3>Reference approach — not yet implemented here</h3>
          <p>The linked Lomma prototype describes an HBV-type rainfall–runoff model: snow accumulation and melt, soil-water storage, fast and slow runoff routing, and an ensemble forecast corrected to the latest observed flow. Its published probability is the share of ensemble members crossing a threshold, not a calibrated probability. This app has no verified basin calibration or forecast series, so it does not calculate or display inflow values yet.</p>
        </div>
      </div>
    </section>
  </div>
}

function StatusCard({ icon: Icon, title, state, detail }: {
  icon: typeof Gauge
  title: string
  state: string
  detail: string
}) {
  return <article className="inflow-status-card">
    <div className="inflow-status-card-top"><span><Icon size={17} aria-hidden="true" /></span><small>{state}</small></div>
    <h2>{title}</h2>
    <p>{detail}</p>
  </article>
}
