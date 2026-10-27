import { AdministrationExperience, type AdministrationStep } from '../../components/AdministrationExperience'
import { CopilotSettingsPanel } from '../../components/CopilotSettingsPanel'
import { MapDataRefreshPanel } from '../../components/MapDataRefreshPanel'
import { fmtElapsed, useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { ENERGY_MAP_ENABLED } from '../appFeatures'
import '../styles/map-data-refresh.css'

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
      title: 'Start telemetry stream',
      why: 'Starts the OPC UA pipeline so live signals flow into the Eventhouse. Takes ~5 min to warm up before signals appear. STID and weather can connect independently.',
      done: data.streamState === 'complete',
      busy: data.streamState === 'running' || Boolean(data.jobs.stream),
      action: 'Start stream',
      run: () => void data.actions.startStream(),
    },
    {
      n: 3,
      title: 'Connect STID',
      why: 'Loads governed facility & asset metadata through the delegated, read-only Lakehouse connector. No GraphQL API is required.',
      done: data.stidState === 'connected',
      busy: data.stidState === 'loading',
      action: 'Connect STID',
      run: () => void data.actions.connectStid(),
    },
    {
      n: 4,
      title: 'Connect telemetry',
      why: 'Reads the latest OPC UA signal values from the Eventhouse stream started in step 2.',
      done: data.telemetryState === 'connected' && data.counts.liveSignals > 0,
      busy: data.telemetryState === 'loading',
      action: 'Connect telemetry',
      run: () => void data.actions.connectTelemetry(),
    },
    {
      n: 5,
      title: 'Connect Weather',
      why: 'Uses UKMet Global Spot forecasts + Land Observations by default, then builds area metrics and serving projections. Shared operating areas are prepared without calling Aurora. Requires the two UKMet API keys in Key Vault. Aurora is optional and disabled by default; its existing key is retained.',
      done: data.weatherState === 'complete',
      busy: data.weatherState === 'running' || Boolean(data.jobs.weather),
      action: 'Connect weather',
      run: () => void data.actions.connectWeather(),
    },
  ]

  return <>
    {data.notice && <div className="notice"><span>{data.notice}</span></div>}
    {Object.entries(data.jobs).filter(([, job]) => job.kind !== 'map').map(([key, job]) => <div key={key} className="v2-progress"><div className="v2-progress-head"><span>{job.label}</span><em>{job.status} · {job.pct}% · {fmtElapsed((job.endedAt ?? data.now) - job.startedAt)}</em></div><div className="v2-progress-track"><div className="v2-progress-bar" style={{ width: `${job.pct}%` }} /></div></div>)}
    <AdministrationExperience steps={steps} />
    {ENERGY_MAP_ENABLED && <MapDataRefreshPanel state={data.mapRefreshState} status={data.jobs.map?.status}
      message={data.mapRefreshMessage} elapsed={data.jobs.map ? fmtElapsed(data.now - data.jobs.map.startedAt) : undefined}
      onStart={() => void data.actions.updateAllMapData()} />}
    <CopilotSettingsPanel />
  </>
}
