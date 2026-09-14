import type { Core } from 'cytoscape'
import { Activity, Box, CircleDot, Database, Focus, GitBranch, Maximize2, Radio, RefreshCw, Search, Wrench, ZoomIn, ZoomOut } from 'lucide-react'
import { useDeferredValue, useMemo, useRef, useState } from 'react'
import { DigitalTwinTree } from '../components/digitalTwin/DigitalTwinTree'
import { buildDigitalTwinTree, pathToAsset } from '../components/digitalTwin/digitalTwinTreeModel'
import { KnowledgeGraphCanvas, type GraphLayout } from '../components/knowledgeGraph/KnowledgeGraphCanvas'
import { buildKnowledgeGraph, type KnowledgeNode, type KnowledgeNodeType } from '../knowledgeGraphModel'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useTheme } from '../hooks/useTheme'
import { useTreeExpansion } from '../hooks/useTreeExpansion'

const NODE_TYPES: Array<{ type: KnowledgeNodeType; label: string }> = [
  { type: 'facility', label: 'Facilities' },
  { type: 'system', label: 'Systems' },
  { type: 'equipment', label: 'Equipment' },
  { type: 'instrument', label: 'Instruments & signals' },
  { type: 'model', label: '3D models' },
  { type: 'work-order', label: 'Work orders' },
  { type: 'inspection', label: 'Inspections' },
  { type: 'notification', label: 'Notifications' },
]

const STATUS_LABEL = { ok: 'Healthy', warn: 'Warning', crit: 'Critical', nodata: 'No live data' }
type GraphScope = 'asset' | 'facility' | 'all'

function navigateTo(tab: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('tab', tab)
  window.history.pushState({}, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function KnowledgeGraphPage() {
  const data = useHydroOperationsData()
  const { theme } = useTheme()
  const controllerRef = useRef<Core | null>(null)
  const [selection, setSelection] = useState<{ nodeId: string; assetId?: string }>()
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query.trim().toLowerCase())
  const [scope, setScope] = useState<GraphScope>('asset')
  const [types, setTypes] = useState<Set<KnowledgeNodeType>>(() => new Set(NODE_TYPES.map(item => item.type)))
  const [statuses, setStatuses] = useState(() => new Set<KnowledgeNode['status']>(['ok', 'warn', 'crit', 'nodata']))
  const [layout, setLayout] = useState<GraphLayout>('breadthfirst')

  const graph = useMemo(() => buildKnowledgeGraph({
    facilities: data.stid?.facilities ?? [],
    equipment: data.stid?.equipment ?? [],
    instruments: data.stid?.instruments ?? [],
    telemetry: data.telemetry,
    workOrders: data.orders,
    inspections: data.inspections,
    notifications: data.notifications,
    models: data.assetModels,
  }), [data.assetModels, data.inspections, data.notifications, data.orders, data.stid, data.telemetry])

  const treeStations = useMemo(() => data.stid ? buildDigitalTwinTree(data.stid) : [], [data.stid])
  const revealPath = useMemo(() => pathToAsset(treeStations, data.selectedAssetId), [data.selectedAssetId, treeStations])
  const expansion = useTreeExpansion(revealPath)
  const assetStatuses = useMemo(() => new Map(graph.nodes.filter(node => node.type === 'equipment').map(node => [node.entityId, node.status])), [graph.nodes])

  const sharedSelectedId = data.selectedAssetId ? `equipment:${data.selectedAssetId}` : undefined
  const effectiveSelectedId = selection?.assetId === data.selectedAssetId && graph.nodes.some(item => item.id === selection.nodeId)
    ? selection.nodeId
    : graph.nodes.some(item => item.id === sharedSelectedId)
      ? sharedSelectedId
      : graph.nodes.find(item => item.type === 'equipment')?.id ?? graph.nodes[0]?.id

  const scopeIds = useMemo(() => {
    if (scope === 'all') return undefined
    const facilityId = data.selectedFacility?.facility_id
    const assetId = data.selectedAssetId
    const facilityEquipmentIds = new Set((data.stid?.equipment ?? []).filter(item => item.facility_id === facilityId).map(item => item.equipment_id))
    if (scope === 'facility') return new Set(graph.nodes.filter(node => node.facilityId === facilityId || (node.equipmentId && facilityEquipmentIds.has(node.equipmentId))).map(node => node.id))
    if (!assetId) return undefined
    const equipmentNodeId = `equipment:${assetId}`
    const visible = new Set(graph.nodes.filter(node => node.id === equipmentNodeId || node.equipmentId === assetId).map(node => node.id))
    const systemIds = new Set<string>()
    for (const edge of graph.edges) {
      if (edge.source === equipmentNodeId && edge.target.startsWith('system:')) systemIds.add(edge.target)
      if (edge.target === equipmentNodeId && edge.source.startsWith('system:')) systemIds.add(edge.source)
    }
    systemIds.forEach(id => visible.add(id))
    if (facilityId) visible.add(`facility:${facilityId}`)
    return visible
  }, [data.selectedAssetId, data.selectedFacility, data.stid, graph.edges, graph.nodes, scope])
  const visibleNodes = useMemo(() => graph.nodes.filter(node => {
    const matchesQuery = !deferredQuery || `${node.label} ${node.entityId} ${node.subtitle} ${Object.values(node.properties).join(' ')}`.toLowerCase().includes(deferredQuery)
    return types.has(node.type) && statuses.has(node.status) && matchesQuery && (!scopeIds || scopeIds.has(node.id))
  }), [deferredQuery, graph.nodes, scopeIds, statuses, types])
  const visibleIds = useMemo(() => new Set(visibleNodes.map(item => item.id)), [visibleNodes])
  const visibleEdges = useMemo(() => graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)), [graph.edges, visibleIds])
  const selectedNode = graph.nodes.find(item => item.id === effectiveSelectedId)

  const selectNode = (nodeId: string) => {
    const node = graph.nodes.find(item => item.id === nodeId)
    setSelection({ nodeId, assetId: node?.equipmentId ?? data.selectedAssetId })
    if (node?.facilityId && node.equipmentId) data.actions.selectAsset(node.facilityId, node.equipmentId)
  }
  const selectAsset = (facilityId: string, assetId: string) => {
    setScope('asset')
    data.actions.selectAsset(facilityId, assetId)
  }
  const toggleType = (type: KnowledgeNodeType) => setTypes(current => {
    const next = new Set(current)
    if (next.has(type)) next.delete(type); else next.add(type)
    return next
  })
  const toggleStatus = (status: KnowledgeNode['status']) => setStatuses(current => {
    const next = new Set(current)
    if (next.has(status)) next.delete(status); else next.add(status)
    return next
  })
  const openRelatedView = (tab: 'digital-twin' | 'telemetry' | 'maintenance') => {
    if (selectedNode?.facilityId && selectedNode.equipmentId) data.actions.selectAsset(selectedNode.facilityId, selectedNode.equipmentId)
    if (tab === 'telemetry' && selectedNode?.type === 'instrument' && selectedNode.facilityId && selectedNode.equipmentId) {
      data.actions.selectTelemetrySignal(selectedNode.facilityId, selectedNode.equipmentId, selectedNode.entityId)
    }
    navigateTo(tab)
  }

  if (data.stidState !== 'connected') return <section className="v2-placeholder-card"><span className="v2-eyebrow">Knowledge Graph</span><h1>Ontology entities are not connected</h1><p className="v2-empty-copy">Use Administration to connect STID. The graph will then combine static ontology entities, bound time-series state, and operational context.</p></section>

  const counts = NODE_TYPES.map(item => ({ ...item, count: graph.nodes.filter(node => node.type === item.type).length }))
  const connectedEdges = selectedNode ? graph.edges.filter(item => item.source === selectedNode.id || item.target === selectedNode.id) : []

  return <div className="kg-page">
    <header className="kg-header">
      <div><span className="v2-eyebrow">Fabric Ontology</span><h1>Operational Knowledge Graph</h1><p>Explore governed topology, bound time-series state, and maintenance context as one semantic network.</p></div>
      <div className="kg-source-state"><span className="kg-live-dot" /><span>Ontology connected</span><strong>{graph.nodes.length} entities · {graph.edges.length} relationships{data.stidSyncedAt ? ` · synced ${new Date(data.stidSyncedAt).toLocaleTimeString()}` : ''}</strong><button type="button" onClick={() => void data.actions.refreshStid()} title="Refresh ontology now"><RefreshCw size={14} /></button></div>
    </header>

    <div className="kg-workspace">
      <aside className="kg-sidebar kg-filters">
        <label className="kg-search"><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Find entity, ID, tag, OPC UA node…" /></label>
        <section className="kg-scope-section"><div className="kg-section-title"><span>Graph scope</span><small>{visibleNodes.length} visible</small></div><div className="kg-segmented">{([['asset', 'Selected'], ['facility', 'Facility'], ['all', 'All']] as const).map(([value, label]) => <button key={value} className={scope === value ? 'active' : ''} onClick={() => setScope(value)}>{label}</button>)}</div></section>
        <div className="kg-asset-tree"><DigitalTwinTree stations={treeStations} selectedAssetId={data.selectedAssetId} handlers={{ isExpanded: expansion.isExpanded, onToggle: expansion.toggle, onSelectAsset: selectAsset, statusOf: assetId => assetStatuses.get(assetId) ?? 'nodata' }} /></div>
        <details className="kg-display-filters"><summary>Display filters</summary><section><div className="kg-section-title"><span>Entity classes</span><button onClick={() => setTypes(new Set(NODE_TYPES.map(item => item.type)))}>All</button></div>{counts.map(item => <label className="kg-filter-row" key={item.type}><input type="checkbox" checked={types.has(item.type)} onChange={() => toggleType(item.type)} /><i className={`kg-type-dot type-${item.type}`} /><span>{item.label}</span><small>{item.count}</small></label>)}</section><section><div className="kg-section-title"><span>Operational health</span></div><div className="kg-status-filters">{(['crit', 'warn', 'ok', 'nodata'] as const).map(status => <button key={status} className={statuses.has(status) ? `active status-${status}` : ''} onClick={() => toggleStatus(status)}><i />{STATUS_LABEL[status]}<small>{graph.nodes.filter(item => item.status === status).length}</small></button>)}</div></section></details>
      </aside>

      <section className="kg-stage">
        <div className="kg-toolbar">
          <div className="kg-layout-control"><GitBranch size={14} /><select value={layout} onChange={event => setLayout(event.target.value as GraphLayout)}><option value="breadthfirst">Hierarchy</option><option value="cose">Semantic network</option><option value="concentric">Concentric</option></select></div>
          <div className="kg-graph-actions"><button title="Zoom out" onClick={() => controllerRef.current?.zoom(controllerRef.current.zoom() * .8)}><ZoomOut size={16} /></button><button title="Zoom in" onClick={() => controllerRef.current?.zoom(controllerRef.current.zoom() * 1.2)}><ZoomIn size={16} /></button><button title="Fit graph" onClick={() => controllerRef.current?.fit(undefined, 48)}><Maximize2 size={16} /></button><button title="Focus selected entity" disabled={!effectiveSelectedId} onClick={() => effectiveSelectedId && controllerRef.current?.animate({ center: { eles: controllerRef.current.getElementById(effectiveSelectedId) }, zoom: 1.25 }, { duration: 300 })}><Focus size={16} /></button></div>
        </div>
        {visibleNodes.length ? <KnowledgeGraphCanvas nodes={visibleNodes} edges={visibleEdges} selectedId={effectiveSelectedId} layout={layout} theme={theme} onSelect={selectNode} controllerRef={controllerRef} /> : <div className="kg-no-results"><CircleDot size={32} /><h2>No matching entities</h2><p>Broaden the entity, health, facility, or search filters.</p></div>}
        <div className="kg-legend"><span><i className="kg-type-dot type-facility" />Facility</span><span><i className="kg-type-dot type-equipment" />Equipment</span><span><i className="kg-type-dot type-instrument" />Instrument</span><span><i className="kg-ring ring-crit" />Critical ring</span><span><i className="kg-ring ring-ok" />Healthy ring</span></div>
      </section>

      <aside className="kg-sidebar kg-inspector">
        {selectedNode ? <>
          <div className="kg-selected-heading"><div className={`kg-selected-node type-${selectedNode.type} status-${selectedNode.status}`}><CircleDot size={20} /></div><div><span className="v2-eyebrow">{selectedNode.type.replace('-', ' ')}</span><h2>{selectedNode.label}</h2><small>{selectedNode.entityId}</small></div></div>
          <div className={`kg-health-banner status-${selectedNode.status}`}><i />{STATUS_LABEL[selectedNode.status]}<span>{selectedNode.subtitle}</span></div>
          {selectedNode.reading && <section className="kg-live-reading"><div><Radio size={15} /><span>Bound time series</span><small>{new Date(selectedNode.reading.eventTime).toLocaleString()}</small></div><strong>{selectedNode.reading.value.toLocaleString()} <small>{String(selectedNode.properties.Unit ?? '')}</small></strong><p>Quality: {selectedNode.reading.quality}</p></section>}
          <section><div className="kg-section-title"><span>Properties</span></div><dl>{Object.entries(selectedNode.properties).filter(([, value]) => value !== undefined && value !== '').map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></section>
          <section><div className="kg-section-title"><span>Connected context</span><small>{connectedEdges.length}</small></div><div className="kg-connections">{connectedEdges.slice(0, 10).map(item => { const otherId = item.source === selectedNode.id ? item.target : item.source; const other = graph.nodes.find(node => node.id === otherId); return other ? <button key={item.id} onClick={() => selectNode(other.id)}><i className={`kg-type-dot type-${other.type}`} /><span><strong>{other.label}</strong><small>{item.label} · {other.type.replace('-', ' ')}</small></span></button> : null })}</div></section>
          <section><div className="kg-section-title"><span>Provenance</span></div><div className="kg-provenance"><Database size={15} /><p>{selectedNode.provenance}</p></div></section>
          {selectedNode.equipmentId && <div className="kg-open-actions"><button onClick={() => openRelatedView('digital-twin')}><Box size={15} />Digital Twin</button><button onClick={() => openRelatedView('telemetry')}><Activity size={15} />Telemetry</button><button onClick={() => openRelatedView('maintenance')}><Wrench size={15} />Maintenance</button></div>}
        </> : <div className="kg-no-selection"><CircleDot size={28} /><h2>Select an entity</h2><p>Inspect properties, bound values, relationships, and provenance.</p></div>}
      </aside>
    </div>
  </div>
}