import type { ReactNode } from 'react'
import { ChevronDown, ChevronRight, Cpu, Factory, Gauge, Radio } from 'lucide-react'
import type { Instrument } from '../../../services/fabric'
import type { Tone } from './TelemetryCards'
import { countSignals, type TelemetryAssetNode, type TelemetrySensorNode, type TelemetryStationNode } from './telemetryTreeModel'

export type TelemetryTreeHandlers = {
  isExpanded: (id: string) => boolean
  onToggle: (id: string) => void
  onSelectSignal: (assetId: string, signalId: string, facilityId: string) => void
  toneOf: (instrument: Instrument) => Tone
}

function TreeBranch({ id, level, icon: Icon, label, meta, isExpanded, onToggle, children }: {
  id: string
  level: number
  icon: typeof Factory
  label: string
  meta: string
  isExpanded: (id: string) => boolean
  onToggle: (id: string) => void
  children: ReactNode
}) {
  const expanded = isExpanded(id)
  return <li className="v2-tree-branch">
    <button type="button" className={`v2-tree-row level-${level}`} aria-expanded={expanded} onClick={() => onToggle(id)}>
      <span className="v2-tree-caret">{expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
      <Icon size={14} />
      <span className="v2-tree-label">{label}</span>
      <span className="v2-tree-meta">{meta}</span>
    </button>
    {expanded && <ul className="v2-tree-children">{children}</ul>}
  </li>
}

function SignalLeaf({ node, assetId, facilityId, selectedSignalId, onSelectSignal, toneOf }: {
  node: TelemetrySensorNode['children'][number]
  assetId: string
  facilityId: string
  selectedSignalId?: string
  onSelectSignal: TelemetryTreeHandlers['onSelectSignal']
  toneOf: TelemetryTreeHandlers['toneOf']
}) {
  const active = node.id === selectedSignalId
  return <li>
    <button
      type="button"
      className={active ? 'v2-tree-row level-3 signal active' : 'v2-tree-row level-3 signal'}
      aria-current={active ? 'true' : undefined}
      onClick={() => onSelectSignal(assetId, node.id, facilityId)}
    >
      <span className="v2-tree-caret" />
      <Radio size={13} />
      <span className="v2-tree-label">{node.label}</span>
      <span className={`v2-tree-dot ${toneOf(node.instrument)}`} aria-hidden="true" />
      <span className="v2-tree-meta">{node.instrument.unit ?? ''}</span>
    </button>
  </li>
}

function SensorBranch({ node, assetId, facilityId, selectedSignalId, handlers }: {
  node: TelemetrySensorNode
  assetId: string
  facilityId: string
  selectedSignalId?: string
  handlers: TelemetryTreeHandlers
}) {
  return <TreeBranch id={node.id} level={2} icon={Cpu} label={node.label} meta={`${node.children.length}`} isExpanded={handlers.isExpanded} onToggle={handlers.onToggle}>
    {node.children.map(signal => <SignalLeaf
      key={signal.id}
      node={signal}
      assetId={assetId}
      facilityId={facilityId}
      selectedSignalId={selectedSignalId}
      onSelectSignal={handlers.onSelectSignal}
      toneOf={handlers.toneOf}
    />)}
  </TreeBranch>
}

function AssetBranch({ node, facilityId, selectedSignalId, handlers }: {
  node: TelemetryAssetNode
  facilityId: string
  selectedSignalId?: string
  handlers: TelemetryTreeHandlers
}) {
  return <TreeBranch id={node.id} level={1} icon={Gauge} label={node.label} meta={`${countSignals(node)}`} isExpanded={handlers.isExpanded} onToggle={handlers.onToggle}>
    {node.children.map(sensor => <SensorBranch key={sensor.id} node={sensor} assetId={node.id} facilityId={facilityId} selectedSignalId={selectedSignalId} handlers={handlers} />)}
  </TreeBranch>
}

function StationBranch({ node, selectedSignalId, handlers }: {
  node: TelemetryStationNode
  selectedSignalId?: string
  handlers: TelemetryTreeHandlers
}) {
  return <TreeBranch id={node.id} level={0} icon={Factory} label={node.label} meta={`${countSignals(node)}`} isExpanded={handlers.isExpanded} onToggle={handlers.onToggle}>
    {node.children.map(asset => <AssetBranch key={asset.id} node={asset} facilityId={node.id} selectedSignalId={selectedSignalId} handlers={handlers} />)}
  </TreeBranch>
}

export function TelemetryTree({ stations, selectedSignalId, handlers }: {
  stations: TelemetryStationNode[]
  selectedSignalId?: string
  handlers: TelemetryTreeHandlers
}) {
  return <nav className="v2-tree-panel" aria-label="Telemetry asset tree">
    <div className="v2-panel-headline"><span className="v2-eyebrow">Assets</span><h2>Signal tree</h2></div>
    {stations.length
      ? <ul className="v2-tree-root">{stations.map(station => <StationBranch key={station.id} node={station} selectedSignalId={selectedSignalId} handlers={handlers} />)}</ul>
      : <p className="v2-empty-copy">No STID assets to enumerate.</p>}
  </nav>
}
