import { AdministrationExperience, type AdministrationStep } from '../../components/AdministrationExperience'
import { fmtElapsed, useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function AdministrationPage() {
  const data = useHydroOperationsData()
  const steps: AdministrationStep[] = [
    {
      n: 1,
      title: 'Sign in to Fabric',
      why: 'Authenticate with your Microsoft Fabric identity — required for operational data and to run setup.',
      done: Boolean(data.user),
      busy: data.operationsState === 'loading',
      action: 'Sign in',
      run: () => void data.actions.authenticate(),
    },
    {
      n: 2,
      title: 'Seed & provision',
      why: 'Loads demo work orders/inspections into SQL and publishes the STID GraphQL API + Data Agent (runs the RTI_011 notebook). Do this before Connect STID.',
      done: data.provisionState === 'complete',
      busy: data.provisionState === 'running' || Boolean(data.jobs.seed),
      action: 'Seed & provision',
      run: () => void data.actions.seedAndProvision(),
    },
    {
      n: 3,
      title: 'Start telemetry stream',
      why: 'Starts the OPC UA pipeline so live signals flow into the Eventhouse. Independent of step 2 — run it in parallel. Takes ~5 min to warm up before signals appear.',
      done: data.streamState === 'complete',
      busy: data.streamState === 'running' || Boolean(data.jobs.stream),
      action: 'Start stream',
      run: () => void data.actions.startStream(),
    },
    {
      n: 4,
      title: 'Connect STID',
      why: 'Loads governed facility & asset metadata from the Lakehouse GraphQL API published in step 2.',
      done: data.stidState === 'connected',
      busy: data.stidState === 'loading',
      action: 'Connect STID',
      run: () => void data.actions.connectStid(),
    },
    {
      n: 5,
      title: 'Connect telemetry',
      why: 'Reads the latest OPC UA signal values from the Eventhouse stream started in step 3.',
      done: data.telemetryState === 'connected' && data.counts.liveSignals > 0,
      busy: data.telemetryState === 'loading',
      action: 'Connect telemetry',
      run: () => void data.actions.connectTelemetry(),
    },
  ]

  return <>
    {data.notice && <div className="notice"><span>{data.notice}</span></div>}
    {Object.entries(data.jobs).map(([key, job]) => <div key={key} className="progress"><div className="progress-head"><span>{job.label}</span><em>{job.status} · {job.pct}% · {fmtElapsed((job.endedAt ?? data.now) - job.startedAt)}</em></div><div className="progress-track"><div className="progress-bar" style={{ width: `${job.pct}%`, marginLeft: 0, animation: 'none' }} /></div></div>)}
    <AdministrationExperience steps={steps} />
  </>
}
