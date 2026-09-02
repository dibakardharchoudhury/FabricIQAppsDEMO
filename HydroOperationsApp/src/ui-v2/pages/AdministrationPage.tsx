import { Check, Database, Play, Radio, RefreshCw, UserRound } from 'lucide-react'
import { FacilityContext } from '../components/FacilityContext'
import { fmtElapsed, useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function AdministrationPage() {
  const data = useHydroOperationsData()
  const operationsStatus = operationalStatusText(data.operationsState, Boolean(data.user))
  const operationsDetail = operationalDetailText(data.operationsState, Boolean(data.user), data.openOrders.length)
  const capabilitySections = [
    {
      n: 1,
      title: 'Fabric and Rayfin session',
      eyebrow: 'Authentication',
      description: data.user ? `Signed in as ${data.user.email}` : 'Authenticate with your Microsoft Fabric identity for Rayfin SQL operations and setup actions.',
      status: data.user ? 'Connected' : statusText(data.operationsState),
      tone: data.user ? 'good' : toneForState(data.operationsState),
      done: Boolean(data.user),
      busy: data.operationsState === 'loading',
      action: 'Sign in',
      icon: UserRound,
      run: () => void data.actions.authenticate(),
      facts: [
        ['Rayfin session', data.user ? 'Authenticated' : statusText(data.operationsState)],
        ['Operational SQL', operationsDetail],
      ],
    },
    {
      n: 2,
      title: 'Seed and provision',
      eyebrow: 'Backend resources',
      description: 'Runs the existing RTI_011 provisioning path for SQL seed data, the STID GraphQL API and Data Agent source.',
      status: data.provisionState === 'complete' ? 'Provisioned' : actionText(data.provisionState),
      tone: toneForAction(data.provisionState),
      done: data.provisionState === 'complete',
      busy: data.provisionState === 'running' || Boolean(data.jobs.seed),
      action: 'Seed & provision',
      icon: Database,
      run: () => void data.actions.seedAndProvision(),
      facts: [
        ['SQL operational data', data.operationsState === 'connected' ? `${data.openOrders.length} open work orders visible` : 'Not loaded'],
        ['Provision job', data.jobs.seed?.status ?? actionText(data.provisionState)],
      ],
    },
    {
      n: 3,
      title: 'Telemetry stream',
      eyebrow: 'Event generation',
      description: 'Starts the existing OPC UA synthetic telemetry pipeline. This can run independently from seed and provision.',
      status: data.streamState === 'complete' ? 'Started' : actionText(data.streamState),
      tone: toneForAction(data.streamState),
      done: data.streamState === 'complete',
      busy: data.streamState === 'running' || Boolean(data.jobs.stream),
      action: 'Start stream',
      icon: Play,
      run: () => void data.actions.startStream(),
      facts: [
        ['Stream job', data.jobs.stream?.status ?? actionText(data.streamState)],
        ['Current telemetry', data.telemetryStatus === 'live' ? `${data.counts.liveSignals} mapped live signals` : 'Start stream, then connect telemetry to verify fresh rows'],
      ],
    },
    {
      n: 4,
      title: 'STID metadata',
      eyebrow: 'Lakehouse GraphQL',
      description: 'Loads facilities, assets, instruments and OPC UA signal metadata from the provisioned GraphQL endpoint.',
      status: data.stidState === 'connected' ? 'Connected' : statusText(data.stidState),
      tone: toneForState(data.stidState),
      done: data.stidState === 'connected',
      busy: data.stidState === 'loading',
      action: 'Connect STID',
      icon: Database,
      run: () => void data.actions.connectStid(),
      facts: [
        ['Facilities', data.counts.facilities || '—'],
        ['Selected facility', data.selectedFacility?.facility_name ?? 'Not selected'],
        ['Assets / instruments', data.stidState === 'connected' ? `${data.counts.assets} / ${data.counts.instruments}` : 'Not loaded'],
      ],
    },
    {
      n: 5,
      title: 'Telemetry connection',
      eyebrow: 'Eventhouse query',
      description: 'Loads live signal values from Eventhouse and maps them through STID opcua_node_id metadata.',
      status: data.telemetryState === 'connected' ? data.telemetryStatusLabel : statusText(data.telemetryState),
      tone: data.telemetryState === 'connected' ? toneForTelemetry(data.telemetryStatus) : toneForState(data.telemetryState),
      done: data.telemetryState === 'connected' && data.counts.liveSignals > 0,
      busy: data.telemetryState === 'loading',
      action: 'Connect telemetry',
      icon: Radio,
      run: () => void data.actions.connectTelemetry(),
      facts: [
        ['Mapped live signals', data.counts.liveSignals || '—'],
        ['Freshness', data.telemetryAgeLabel || 'No telemetry loaded'],
        ['Quality issues', data.counts.qualityIssues],
      ],
    },
  ]

  return <div className="v2-admin">
    <FacilityContext />
    <section className="v2-admin-head">
      <span className="v2-eyebrow">Administration</span>
      <h1>Fabric environment setup</h1>
      <p>Operate the same Fabric, Eventhouse, Lakehouse and Rayfin SQL setup flow used by the current Hydro Operations UI.</p>
    </section>

    {data.notice && <div className="v2-notice"><span>{data.notice}</span></div>}

    {Object.entries(data.jobs).map(([key, job]) => <div key={key} className="v2-progress">
      <div className="v2-progress-head"><span>{job.label}</span><em>{job.status} · {job.pct}% · {fmtElapsed((job.endedAt ?? data.now) - job.startedAt)}</em></div>
      <div className="v2-progress-track"><div className="v2-progress-bar" style={{ width: `${job.pct}%` }} /></div>
    </div>)}

    <section className="v2-admin-status-grid" aria-label="Current setup status">
      <StatusCard label="Rayfin operational SQL" value={operationsStatus} detail={operationsDetail} tone={data.operationsState === 'connected' ? 'good' : toneForState(data.operationsState)} />
      <StatusCard label="STID metadata" value={data.stidState === 'connected' ? 'Connected' : statusText(data.stidState)} detail={data.stidState === 'connected' ? `${data.counts.facilities} facilities · ${data.counts.assets} assets · ${data.counts.instruments} instruments` : 'Connect to Lakehouse GraphQL'} tone={toneForState(data.stidState)} />
      <StatusCard label="Telemetry" value={data.telemetryState === 'connected' ? data.telemetryStatusLabel : statusText(data.telemetryState)} detail={data.counts.liveSignals ? `${data.counts.liveSignals} mapped signals · ${data.counts.qualityIssues} quality issues` : 'Connect to Eventhouse'} tone={data.telemetryState === 'connected' ? toneForTelemetry(data.telemetryStatus) : toneForState(data.telemetryState)} />
    </section>

    <section className="v2-admin-section">
      <div className="v2-admin-section-head">
        <span className="v2-eyebrow">Setup Operations</span>
        <h2>Manage environment readiness</h2>
      </div>
      <div className="v2-capability-grid">
      {capabilitySections.map(step => {
        const Icon = step.icon
        return <article key={step.n} className={`v2-capability-card ${step.tone}`}>
          <div className="v2-capability-top">
            <span className="v2-step-num">{step.done ? <Check size={14} /> : step.n}</span>
            <span className="v2-step-icon"><Icon size={17} /></span>
            <span className={`v2-status-pill ${step.tone}`}>{step.status}</span>
          </div>
          <div className="v2-step-body"><span className="v2-card-eyebrow">{step.eyebrow}</span><strong>{step.title}</strong><small>{step.description}</small></div>
          <dl className="v2-capability-facts">{step.facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          <button className="v2-step-action" type="button" onClick={step.run} disabled={step.busy || step.done}>{step.done ? 'Done' : step.busy ? 'Working...' : step.action}</button>
        </article>
      })}
      </div>
    </section>

    <section className="v2-admin-section compact">
      <div className="v2-admin-section-head">
        <span className="v2-eyebrow">Refresh</span>
        <h2>Operational data</h2>
      </div>
      <div className="v2-admin-refresh-row">
        <p>Reload Rayfin work orders using the existing Rayfin session and data client. This does not run provisioning or change data.</p>
        <button className="v2-step-action" type="button" onClick={() => void data.actions.refreshOperationalData()} disabled={data.operationsState === 'loading'}><RefreshCw size={13} /> Refresh work orders</button>
      </div>
    </section>
  </div>
}

function StatusCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: string }) {
  return <article className={`v2-status-card ${tone}`}>
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{detail}</small>
  </article>
}

function statusText(state: string) {
  if (state === 'connected') return 'Connected'
  if (state === 'loading') return 'Checking'
  if (state === 'error') return 'Error'
  if (state === 'unavailable') return 'Not connected'
  return 'Not checked'
}

function operationalStatusText(state: string, hasUser: boolean) {
  if (state === 'connected') return 'Connected'
  if (state === 'loading') return hasUser ? 'Loading work orders' : 'Checking'
  if (state === 'error') return hasUser ? 'Work orders unavailable' : 'Error'
  if (hasUser) return 'Session authenticated'
  if (state === 'unavailable') return 'Not connected'
  return 'Not checked'
}

function operationalDetailText(state: string, hasUser: boolean, openOrderCount: number) {
  if (state === 'connected') return `${openOrderCount} open work orders loaded`
  if (state === 'loading') return hasUser ? 'Loading work orders from Rayfin SQL' : 'Checking Rayfin session'
  if (state === 'error') return hasUser ? 'Rayfin session is authenticated; work orders could not be loaded' : 'Rayfin session could not be established'
  return hasUser ? 'Rayfin session is authenticated; work orders are not loaded yet' : 'Sign in to load operational records'
}

function actionText(state: string) {
  if (state === 'complete') return 'Complete'
  if (state === 'running') return 'Running'
  if (state === 'error') return 'Error'
  return 'Not run'
}

function toneForState(state: string) {
  if (state === 'connected') return 'good'
  if (state === 'loading') return 'warn'
  if (state === 'error') return 'bad'
  return 'muted'
}

function toneForAction(state: string) {
  if (state === 'complete') return 'good'
  if (state === 'running') return 'warn'
  if (state === 'error') return 'bad'
  return 'muted'
}

function toneForTelemetry(state: string) {
  if (state === 'live') return 'good'
  if (state === 'delayed') return 'warn'
  if (state === 'stale') return 'bad'
  return 'muted'
}