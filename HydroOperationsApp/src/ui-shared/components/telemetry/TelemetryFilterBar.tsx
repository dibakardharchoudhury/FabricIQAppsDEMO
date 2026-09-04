import type { Equipment, Instrument } from '../../../services/fabric'

export function TelemetryFilterBar({ assets, assetId, onAssetChange, signals, signalId, onSignalChange }: {
  assets: Equipment[]
  assetId?: string
  onAssetChange: (id: string) => void
  signals: Instrument[]
  signalId?: string
  onSignalChange: (id: string) => void
}) {
  return <div className="v2-filter-bar">
    <label>
      <span>Asset</span>
      <select value={assetId ?? ''} onChange={event => onAssetChange(event.target.value)}>
        {assets.map(item => <option key={item.equipment_id} value={item.equipment_id}>{item.tag ?? item.equipment_id}</option>)}
      </select>
    </label>
    <label>
      <span>Signal</span>
      <select value={signalId ?? ''} onChange={event => onSignalChange(event.target.value)} disabled={!signals.length}>
        {signals.map(item => <option key={item.instrument_id} value={item.instrument_id}>{item.tag ?? item.instrument_id}</option>)}
      </select>
    </label>
  </div>
}
