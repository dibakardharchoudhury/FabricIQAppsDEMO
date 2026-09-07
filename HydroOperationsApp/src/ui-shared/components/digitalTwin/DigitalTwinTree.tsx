import { ChevronDown, ChevronRight, Factory, Gauge } from 'lucide-react'
import type { TwinStatus } from '../../../twin'
import type { DigitalTwinAssetNode, DigitalTwinStationNode } from './digitalTwinTreeModel'

const statusLabel: Record<TwinStatus, string> = {
  ok: 'OK',
  warn: 'Uncertain',
  crit: 'Critical',
  nodata: 'No data',
}
const statusOrder: TwinStatus[] = ['crit', 'warn', 'ok', 'nodata']

export type DigitalTwinTreeHandlers = {
  isExpanded: (id: string) => boolean
  onToggle: (id: string) => void
  onSelectAsset: (facilityId: string, assetId: string) => void
  statusOf: (assetId: string) => TwinStatus
}

function AssetLeaf({ node, facilityId, selectedAssetId, handlers }: {
  node: DigitalTwinAssetNode
  facilityId: string
  selectedAssetId?: string
  handlers: DigitalTwinTreeHandlers
}) {
  const active = node.id === selectedAssetId
  const status = handlers.statusOf(node.id)
  return <li>
    <button
      type="button"
      className={active ? 'v2-tree-row level-1 status active' : 'v2-tree-row level-1 status'}
      aria-current={active ? 'true' : undefined}
      aria-label={`${node.label}, ${statusLabel[status]}`}
      onClick={() => handlers.onSelectAsset(facilityId, node.id)}
    >
      <span className="v2-tree-caret" />
      <Gauge size={14} />
      <span className="v2-tree-label">{node.label}</span>
      <span className={`v2-tree-dot ${status}`} aria-hidden="true" />
      <span className="v2-tree-meta">{statusLabel[status]}</span>
    </button>
  </li>
}

function StationBranch({ node, selectedAssetId, handlers }: {
  node: DigitalTwinStationNode
  selectedAssetId?: string
  handlers: DigitalTwinTreeHandlers
}) {
  const expanded = handlers.isExpanded(node.id)
  const counts = node.children.reduce<Record<TwinStatus, number>>((result, asset) => {
    result[handlers.statusOf(asset.id)]++
    return result
  }, { ok: 0, warn: 0, crit: 0, nodata: 0 })
  const aggregateLabel = statusOrder
    .filter(status => counts[status])
    .map(status => `${counts[status]} ${statusLabel[status].toLowerCase()}`)
    .join(', ')
  return <li className="v2-tree-branch">
    <button type="button" className="v2-tree-row level-0 station-status" aria-expanded={expanded} aria-label={`${node.label}, ${aggregateLabel}`} onClick={() => handlers.onToggle(node.id)}>
      <span className="v2-tree-caret">{expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
      <Factory size={14} />
      <span className="v2-tree-label">{node.label}</span>
      <span className="v2-tree-status-summary" aria-hidden="true">
        {statusOrder.filter(status => counts[status]).map(status => <span key={status}><i className={`v2-tree-dot ${status}`} />{counts[status]}</span>)}
      </span>
    </button>
    {expanded && <ul className="v2-tree-children">{node.children.map(asset => <AssetLeaf key={asset.id} node={asset} facilityId={node.id} selectedAssetId={selectedAssetId} handlers={handlers} />)}</ul>}
  </li>
}

export function DigitalTwinTree({ stations, selectedAssetId, handlers }: {
  stations: DigitalTwinStationNode[]
  selectedAssetId?: string
  handlers: DigitalTwinTreeHandlers
}) {
  return <nav className="v2-tree-panel" aria-label="Digital Twin asset tree">
    <div className="v2-panel-headline"><span className="v2-eyebrow">Assets</span><h2>Asset tree</h2></div>
    {stations.length
      ? <ul className="v2-tree-root">{stations.map(station => <StationBranch key={station.id} node={station} selectedAssetId={selectedAssetId} handlers={handlers} />)}</ul>
      : <p className="v2-empty-copy">No STID assets to enumerate.</p>}
  </nav>
}