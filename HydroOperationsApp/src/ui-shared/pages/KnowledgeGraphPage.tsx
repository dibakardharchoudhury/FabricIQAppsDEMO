import type { Core } from 'cytoscape'
import { Activity, Box, CircleDot, Database, Focus, GitBranch, Maximize2, Radio, RefreshCw, Search, Wrench, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { queryOntologyContract, queryOntologyGraph, type OntologyContract, type OntologyGraph } from '../../services/fabric'
import { DigitalTwinTree } from '../components/digitalTwin/DigitalTwinTree'
import { buildDigitalTwinTree, pathToAsset } from '../components/digitalTwin/digitalTwinTreeModel'
import { KnowledgeGraphCanvas, type GraphLayout } from '../components/knowledgeGraph/KnowledgeGraphCanvas'
import { buildKnowledgeGraph, isExactKnowledgeNodeMatch, knowledgeGraphScope, loadNativeGraphSnapshot, matchesKnowledgeNodeQuery, type KnowledgeNode, type KnowledgeNodeType } from '../knowledgeGraphModel'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useTheme } from '../hooks/useTheme'
import { useTreeExpansion } from '../hooks/useTreeExpansion'

const NODE_TYPES: Array<{ type: KnowledgeNodeType; label: string }> = [
  { type: 'facility', label: 'Facilities' },
  { type: 'system', label: 'Systems' },
  { type: 'equipment', label: 'Equipment' },
  { type: 'instrument', label: 'Instruments & signals' },
  { type: 'signal', label: 'Time-series signals' },
  { type: 'ontology', label: 'Other Ontology entities' },
  { type: 'model', label: '3D models' },
  { type: 'work-order', label: 'Work orders' },
  { type: 'inspection', label: 'Inspections' },
  { type: 'notification', label: 'Notifications' },
]

const STATUS_LABEL = { ok: 'Healthy', warn: 'Warning', crit: 'Critical', nodata: 'No live data' }
const LAYOUT_DESCRIPTION: Record<GraphLayout, string> = {
  breadthfirst: 'Hierarchy: arrange the governed relationship chain in directional levels.',
  cose: 'Semantic network: cluster connected entities with a force-directed layout.',
  concentric: 'Concentric: arrange entities in rings around the most connected hubs.',
}
type GraphScope = 'asset' | 'facility' | 'all'

const graphVersion = (graph: OntologyGraph | null) => graph ? JSON.stringify({
  ontologyId: graph.ontologyId,
  graphModelId: graph.graphModelId,
  graphModelName: graph.graphModelName,
  nodes: graph.nodes.map(item => [item.oid, item.labels, item.entityTypeId, item.properties]),
  edges: graph.edges.map(item => [item.oid, item.labels, item.relationshipTypeId, item.sourceOid, item.targetOid, item.properties]),
}) : ''

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
  const [ontologyGraph, setOntologyGraph] = useState<OntologyGraph | null>(null)
  const [ontology, setOntology] = useState<OntologyContract | null>(null)
  const [graphLoading, setGraphLoading] = useState(true)
  const [graphQueriedAt, setGraphQueriedAt] = useState<number>()
  const [graphError, setGraphError] = useState<string>()
  const graphRefreshVersion = useRef(0)
  const cancelGraphQuery = useCallback(() => { graphRefreshVersion.current++ }, [])

  const loadOntologyGraph = useCallback(async () => {
    const version = ++graphRefreshVersion.current
    setGraphLoading(true)
    try {
      const { ontology: contract, ontologyGraph: next } = await loadNativeGraphSnapshot(queryOntologyGraph, queryOntologyContract)
      if (version !== graphRefreshVersion.current) return
      setOntology(contract)
      setOntologyGraph(current => graphVersion(current) === graphVersion(next) ? current : next)
      setGraphQueriedAt(next ? Date.now() : undefined)
      setGraphError(next ? undefined : 'No verified native backing graph is loaded for the selected Ontology. Materialize it through Manage graph in the Ontology portal, then configure the verified workspace, Ontology, and graph mapping and retry.')
    } catch (error) {
      if (version !== graphRefreshVersion.current) return
      setOntologyGraph(null)
      setOntology(null)
      setGraphQueriedAt(undefined)
      setGraphError(error instanceof Error ? error.message : 'Ontology graph query failed. Refresh and check Fabric access.')
    }
    finally { if (version === graphRefreshVersion.current) setGraphLoading(false) }
  }, [])

  const refreshAll = async () => {
    data.actions.refreshDiscovery()
    await loadOntologyGraph()
  }

  useEffect(() => {
    let inFlight = false
    const refresh = async () => {
      if (inFlight || document.hidden) return
      inFlight = true
      try { await loadOntologyGraph() }
      finally { inFlight = false }
    }
    void refresh()
    const interval = window.setInterval(refresh, 30_000)
    const handleVisibility = () => { if (!document.hidden) void refresh() }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      cancelGraphQuery()
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [cancelGraphQuery, loadOntologyGraph])

  const graph = useMemo(() => buildKnowledgeGraph({
    facilities: [],
    systems: [],
    equipment: [],
    instruments: [],
    telemetry: data.telemetry,
    workOrders: data.orders,
    inspections: data.inspections,
    notifications: data.notifications,
    models: data.assetModels,
    ontology,
    ontologyGraph,
  }), [data.assetModels, data.inspections, data.notifications, data.orders, data.telemetry, ontology, ontologyGraph])

  const sharedSelectedId = data.selectedAssetId ? `equipment:${data.selectedAssetId}` : undefined
  const effectiveSelectedId = selection && graph.nodes.some(item => item.id === selection.nodeId)
    && (selection.assetId === data.selectedAssetId || graph.nodes.find(item => item.id === selection.nodeId)?.equipmentId === data.selectedAssetId)
    ? selection.nodeId
    : graph.nodes.some(item => item.id === sharedSelectedId)
      ? sharedSelectedId
      : graph.nodes.find(item => item.type === 'equipment')?.id ?? graph.nodes[0]?.id

  const treeStations = useMemo(() => buildDigitalTwinTree({
    facilities: graph.nodes.filter(node => node.type === 'facility').map(node => ({ facility_id: node.entityId, facility_name: node.label })),
    equipment: graph.nodes.filter(node => node.type === 'equipment' && node.facilityId).map(node => ({
      equipment_id: node.entityId, facility_id: node.facilityId!, system_id: '', tag: node.label,
    })),
  }), [graph.nodes])
  const selectedAssetId = graph.nodes.find(node => node.id === effectiveSelectedId)?.equipmentId ?? data.selectedAssetId
  const revealPath = useMemo(() => pathToAsset(treeStations, selectedAssetId), [selectedAssetId, treeStations])
  const expansion = useTreeExpansion(revealPath)
  const assetStatuses = useMemo(() => new Map(graph.nodes.filter(node => node.type === 'equipment').map(node => [node.entityId, node.status])), [graph.nodes])
  const scopeIds = useMemo(() => knowledgeGraphScope(graph, scope, effectiveSelectedId, selectedAssetId), [effectiveSelectedId, graph, scope, selectedAssetId])
  const visibleNodes = useMemo(() => graph.nodes.filter(node => {
    const matchesQuery = matchesKnowledgeNodeQuery(node, deferredQuery)
    const matchesScope = Boolean(deferredQuery) || !scopeIds || scopeIds.has(node.id)
    return types.has(node.type) && statuses.has(node.status) && matchesQuery && matchesScope
  }), [deferredQuery, graph.nodes, scopeIds, statuses, types])
  const visibleIds = useMemo(() => new Set(visibleNodes.map(item => item.id)), [visibleNodes])
  const visibleEdges = useMemo(() => graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)), [graph.edges, visibleIds])
  const selectedNode = graph.nodes.find(item => item.id === effectiveSelectedId)

  const selectNode = (nodeId: string) => {
    const node = graph.nodes.find(item => item.id === nodeId)
    setSelection({ nodeId, assetId: data.selectedAssetId })
    if (node?.facilityId && node.equipmentId) data.actions.selectAsset(node.facilityId, node.equipmentId)
  }
  const selectAsset = (facilityId: string, assetId: string) => {
    setScope('asset')
    const node = graph.nodes.find(item => item.type === 'equipment' && item.entityId === assetId)
    if (node) setSelection({ nodeId: node.id, assetId: data.selectedAssetId })
    data.actions.selectAsset(facilityId, assetId)
  }
  const updateQuery = (value: string) => {
    setQuery(value)
    const exactMatches = graph.nodes.filter(node => isExactKnowledgeNodeMatch(node, value))
    if (exactMatches.length === 1) selectNode(exactMatches[0].id)
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

  if (!ontologyGraph && graphLoading) return <section className="v2-placeholder-card"><span className="v2-eyebrow">Knowledge Graph</span><h1>Loading the Ontology graph</h1><p className="v2-empty-copy">Querying the Fabric Graph Model for governed entities and relationships.</p></section>
  if (graphError || graph.error || !ontologyGraph) return <section className="v2-placeholder-card"><span className="v2-eyebrow">Knowledge Graph</span><h1>Ontology graph is unavailable</h1><p className="v2-empty-copy" role="alert">{graphError ?? graph.error ?? 'Sign in with Fabric item read and execute access, then refresh the graph.'}</p><button type="button" onClick={() => void refreshAll()}>Retry graph query</button></section>

  const counts = NODE_TYPES.map(item => ({ ...item, count: graph.nodes.filter(node => node.type === item.type).length }))
  const connectedEdges = selectedNode ? graph.edges.filter(item => item.source === selectedNode.id || item.target === selectedNode.id) : []

  return <div className="kg-page">
    <header className="kg-header">
      <div><span className="v2-eyebrow">Fabric Ontology</span><h1>Operational Knowledge Graph</h1><p>Explore governed topology, bound time-series state, and maintenance context as one semantic network.</p></div>
      <div className="kg-source-state"><span className="kg-live-dot" /><span>{`${ontologyGraph.graphModelName} · GQL`}</span><strong>{graph.nodes.length} entities · {graph.edges.length} relationships{graphQueriedAt ? ` · queried ${new Date(graphQueriedAt).toLocaleTimeString()}` : ''}</strong><button type="button" onClick={() => void refreshAll()} title="Refresh graph, telemetry joins, and Ontology contract"><RefreshCw size={14} /></button></div>
    </header>

    <div className="kg-workspace">
      <aside className="kg-sidebar kg-filters">
        <label className="kg-search"><Search size={15} /><input value={query} onChange={event => updateQuery(event.target.value)} placeholder="Find entity, ID, tag, OPC UA node…" /></label>
        <section className="kg-scope-section"><div className="kg-section-title"><span>Graph scope</span><small>{visibleNodes.length} visible</small></div><div className="kg-segmented">{([['asset', 'Selected'], ['facility', 'Facility'], ['all', 'All']] as const).map(([value, label]) => <button key={value} className={scope === value ? 'active' : ''} onClick={() => setScope(value)}>{label}</button>)}</div></section>
        <div className="kg-asset-tree"><DigitalTwinTree stations={treeStations} selectedAssetId={selectedAssetId} handlers={{ isExpanded: expansion.isExpanded, onToggle: expansion.toggle, onSelectAsset: selectAsset, statusOf: assetId => assetStatuses.get(assetId) ?? 'nodata' }} /></div>
        <details className="kg-display-filters"><summary>Display filters</summary><section><div className="kg-section-title"><span>Entity classes</span><button onClick={() => setTypes(new Set(NODE_TYPES.map(item => item.type)))}>All</button></div>{counts.map(item => <label className="kg-filter-row" key={item.type}><input type="checkbox" checked={types.has(item.type)} onChange={() => toggleType(item.type)} /><i className={`kg-type-dot type-${item.type}`} /><span>{item.label}</span><small>{item.count}</small></label>)}</section><section><div className="kg-section-title"><span>Operational health</span></div><div className="kg-status-filters">{(['crit', 'warn', 'ok', 'nodata'] as const).map(status => <button key={status} className={statuses.has(status) ? `active status-${status}` : ''} onClick={() => toggleStatus(status)}><i />{STATUS_LABEL[status]}<small>{graph.nodes.filter(item => item.status === status).length}</small></button>)}</div></section></details>
      </aside>

      <section className="kg-stage">
        <div className="kg-toolbar">
          <div className="kg-layout-control"><GitBranch size={14} /><select value={layout} title={LAYOUT_DESCRIPTION[layout]} aria-label={`Graph layout. ${LAYOUT_DESCRIPTION[layout]}`} onChange={event => setLayout(event.target.value as GraphLayout)}><option value="breadthfirst">Hierarchy</option><option value="cose">Semantic network</option><option value="concentric">Concentric</option></select></div>
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