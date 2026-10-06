import { Database, RefreshCw } from 'lucide-react'

export function MapDataRefreshPanel({ state, status, message, elapsed, onStart }: {
  state: 'idle' | 'running' | 'complete' | 'error'
  status?: string
  message?: string
  elapsed?: string
  onStart: () => void
}) {
  const running = state === 'running'
  return <section className="map-data-refresh" aria-labelledby="map-data-refresh-title">
    <header>
      <Database size={22} />
      <div><span className="eyebrow">MAP DATA</span><h2 id="map-data-refresh-title">Update map data sources</h2></div>
      <button type="button" onClick={onStart} disabled={running}>
        <RefreshCw size={16} />{running ? 'Updating map data...' : 'Update all map data'}
      </button>
    </header>
    <p>Start <code>Geo_001_ingest_energy_context</code> through <code>04_Pipe_EnergyMap</code>.
      When map chat is enabled, <code>Geo_002_publish_map_agent</code> follows ingestion to refresh its data and republish the map agent.</p>
    <div className="map-data-refresh-status" role={state === 'error' ? 'alert' : 'status'} aria-live="polite">
      <strong>{running ? status || 'Starting' : state === 'complete' ? 'Completed' : state === 'error' ? 'Update needs attention' : 'Ready to update'}</strong>
      {running && <><progress aria-label="Map data update is running" /><span>{elapsed}</span></>}
      {message && <p>{message}</p>}
    </div>
    <details>
      <summary>Included sources and refresh steps</summary>
      <ul>
        <li><strong>NVE Nettanlegg4:</strong> power lines, subsea cables, masts and transformer stations.</li>
        <li><strong>NVE hydropower:</strong> plant locations, owners, capacity and technical properties.</li>
        <li><strong>NVE reservoir statistics:</strong> current area figures and reservoir-area overlays.</li>
        <li><strong>Statnett:</strong> country balance, power exchange and the stored frequency snapshot.</li>
        <li><strong>Nord Pool UMM:</strong> market messages, importance rankings and asset-message links.</li>
        <li><strong>Map chat, when enabled:</strong> typed data projections, source readiness and the dedicated Data Agent.</li>
      </ul>
    </details>
    <p className="map-data-refresh-help">A new run bypasses the 24-hour source cache and may take tens of minutes on the Fabric capacity.
      Existing runs are monitored rather than duplicated. Progress resumes after reloading this app; closing a tab does not cancel the cloud run.
      Last-good source data is retained on import failure. No recurring schedule is enabled.</p>
    <p className="map-data-refresh-help">The live frequency tile continues to fetch on demand; this action updates its separate stored snapshot.
      The pinned country-boundary dataset is reused. Synthetic demo assets and telemetry are not reset.</p>
  </section>
}
