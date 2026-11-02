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
  if (ENERGY_MAP_ENABLED) steps.push({
    n: 6,
    title: 'Prepare Norwegian Energy Map',
    why: 'Included in core workspace setup. Prepare once, then refresh operational snapshots here when needed. Cached national inventories are reused; map chat is verified when enabled.',
    done: data.mapPrepared,
    busy: data.mapRefreshState === 'running' || Boolean(data.jobs.map),
    action: data.mapPrepared ? 'Refresh snapshots' : 'Prepare map',
    run: () => void data.actions.refreshMapData(data.mapPrepared ? 'operational' : 'prepare'),
    repeatable: true,
    details: <MapDataRefreshPanel state={data.mapRefreshState} message={data.mapRefreshMessage}
      onRebuild={() => void data.actions.refreshMapData('rebuild')} />,
  })

  return <>
    {data.notice && <div className="notice"><span>{data.notice}</span></div>}
    {Object.entries(data.jobs).map(([key, job]) => <div key={key} className="v2-progress">
      <div className="v2-progress-head">
        <span>{job.kind === 'map' ? data.mapRefreshMessage || job.label : job.label}</span>
        <em>{job.status} · {job.kind !== 'map' && `${job.pct}% · `}{fmtElapsed((job.endedAt ?? data.now) - job.startedAt)}</em>
      </div>
      <div className="v2-progress-track" role="progressbar" aria-label={job.label}
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.kind === 'map' ? undefined : job.pct}>
        <div className={`v2-progress-bar${job.kind === 'map' ? ' v2-progress-bar--indeterminate' : ''}`}
          style={job.kind === 'map' ? undefined : { width: `${job.pct}%` }} />
      </div>
    </div>)}
    <AdministrationExperience steps={steps} />
    <CopilotSettingsPanel />
  </>
}
