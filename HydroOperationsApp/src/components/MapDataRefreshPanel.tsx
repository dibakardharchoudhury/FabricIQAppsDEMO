export function MapDataRefreshPanel({ state, status, message, elapsed, onRebuild }: {
  state: 'idle' | 'running' | 'complete' | 'error'
  status?: string
  message?: string
  elapsed?: string
  onRebuild: () => void
}) {
  const running = state === 'running'
  return <div className="map-data-refresh">
    {state !== 'idle' && <div className="map-data-refresh-status" role={state === 'error' ? 'alert' : 'status'} aria-live="polite">
      <strong>{running ? status || 'Starting' : state === 'complete' ? 'Completed' : state === 'error' ? 'Update needs attention' : 'Ready to update'}</strong>
      {running && <><progress aria-label="Map data update is running" /><span>{elapsed}</span></>}
      {message && <p>{message}</p>}
    </div>}
    <details>
      <summary>Maintenance and source details</summary>
      <p>Routine refresh updates reservoir statistics, power balance, exchange, stored frequency and market messages;
        it skips the large national network and hydropower inventories.
        <code>04_Pipe_EnergyMap</code> then prepares map-chat projections when enabled. Agent configuration is published only when changed.</p>
      <ul>
        <li><strong>NVE Nettanlegg4:</strong> power lines, subsea cables, masts and transformer stations.</li>
        <li><strong>NVE hydropower:</strong> plant locations, owners, capacity and technical properties.</li>
        <li><strong>NVE reservoir statistics:</strong> current area figures and reservoir-area overlays.</li>
        <li><strong>Statnett:</strong> country balance, power exchange and the stored frequency snapshot.</li>
        <li><strong>Nord Pool UMM:</strong> market messages, importance rankings and asset-message links.</li>
        <li><strong>Map chat, when enabled:</strong> typed data projections, source readiness and the dedicated Data Agent.</li>
      </ul>
      <details className="map-data-refresh-advanced">
      <summary>Advanced: full national source rebuild</summary>
      <p>Use only when national inventories need a forced reload. This bypasses the 24-hour source cache,
        downloads every network and plant layer again, and can take tens of minutes. It is not routine maintenance.</p>
      <button type="button" onClick={onRebuild} disabled={running}>Rebuild all map sources</button>
      </details>
    <p className="map-data-refresh-help">Operational updates still require Fabric notebook startup and map-chat readiness checks.
      Existing runs are monitored rather than duplicated. Progress resumes after reloading this app; closing a tab does not cancel the cloud run.
      Last-good source data is retained on import failure. No recurring schedule is enabled.</p>
    <p className="map-data-refresh-help">The live frequency tile continues to fetch on demand; this action updates its separate stored snapshot.
      The pinned country-boundary dataset is reused. Synthetic demo assets and telemetry are not reset.</p>
    </details>
  </div>
}
