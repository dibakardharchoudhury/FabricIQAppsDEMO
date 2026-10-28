import type { Core } from 'cytoscape'
import { Activity, Box, ChevronRight, CircleDot, Database, Download, Focus, GitBranch, Maximize2, Minimize2, Radio, RefreshCw, Scan, Search, ShieldCheck, Wrench, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { connectOntologyAccess, queryOntologyContract, queryOntologyGraph, type OntologyContract, type OntologyGraph } from '../../services/fabric'
import { knowledgeGraphExportFileName, serializeNativeKnowledgeGraph, type KnowledgeGraphExportFormat } from '../../services/knowledgeGraphExport'
import { subscribeWorkOrdersChanged } from '../../services/rayfin'
import { ageLabel } from '../../twin'
import { DigitalTwinTree } from '../components/digitalTwin/DigitalTwinTree'
import { buildDigitalTwinTree, pathToAsset } from '../components/digitalTwin/digitalTwinTreeModel'
import { KnowledgeGraphCanvas, type GraphLayout } from '../components/knowledgeGraph/KnowledgeGraphCanvas'
import { buildKnowledgeGraph, isExactKnowledgeNodeMatch, knowledgeGraphFocusId, knowledgeGraphScope, loadNativeGraphSnapshot, matchesKnowledgeNodeQuery, summarizeKnowledgeGraph, type KnowledgeNode, type KnowledgeNodeType } from '../knowledgeGraphModel'
import { knowledgeTelemetryState } from '../knowledgeGraphVisuals'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useExpandedView } from '../hooks/useExpandedView'
import { useTheme } from '../hooks/useTheme'
import { useTreeExpansion } from '../hooks/useTreeExpansion'
import { OntologyModelView } from './OntologyModelView'

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
  breadthfirst: 'Hierarchy: arrange facilities above systems, equipment and their context. Arrows retain the Ontology relationship direction.',
  cose: 'Semantic network: cluster connected entities with a force-directed layout.',
  concentric: 'Concentric: arrange entities in rings around the most connected hubs.',
}
type GraphScope = 'asset' | 'facility' | 'all'
const GRAPH_REFRESH_POLL_MS = 5 * 60_000

function navigateTo(tab: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('tab', tab)
  window.history.pushState({}, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function KnowledgeGraphPage() {
  const [view, setView] = useState<'ontology' | 'native'>('ontology')
  return <div>
    <div className="kg-view-choice" role="group" aria-label="Knowledge Graph view">
      <button type="button" aria-pressed={view === 'ontology'} onClick={() => setView('ontology')}>Ontology model</button>
      <button type="button" aria-pressed={view === 'native'} onClick={() => setView('native')}>Native asset instances</button>
    </div>
    {view === 'ontology' ? <OntologyModelView /> : <NativeKnowledgeGraphPage />}
  </div>
}

function NativeKnowledgeGraphPage() {
  const data = useHydroOperationsData()
  const { theme } = useTheme()
  const graphView = useExpandedView()
  const controllerRef = useRef<Core | null>(null)
  const [selection, setSelection] = useState<{ nodeId: string; assetId?: string }>()
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query.trim().toLowerCase())
  const [scope, setScope] = useState<GraphScope>('asset')
  const [types, setTypes] = useState<Set<KnowledgeNodeType>>(() => new Set(NODE_TYPES.map(item => item.type)))
  const [statuses, setStatuses] = useState(() => new Set<KnowledgeNode['status']>(['ok', 'warn', 'crit', 'nodata']))
  const [layout, setLayout] = useState<GraphLayout>('breadthfirst')
  const [loadedGraph, setOntologyGraph] = useState<OntologyGraph | null>(null)
  const [loadedUserId, setLoadedUserId] = useState<string>()
  const userId = data.user?.id
  const ontologyGraph = loadedUserId === userId ? loadedGraph : null
  const [ontology, setOntology] = useState<OntologyContract | null>(null)
  const [graphLoading, setGraphLoading] = useState(true)
  const [graphQueriedAt, setGraphQueriedAt] = useState<number>()
  const [graphError, setGraphError] = useState<string>()
  const [graphLoadMs, setGraphLoadMs] = useState<number>()
  const [exportFormat, setExportFormat] = useState<KnowledgeGraphExportFormat>('owl-turtle')
  const [exportError, setExportError] = useState<string>()
  const graphRefreshVersion = useRef(0)
  const cancelGraphQuery = useCallback(() => { graphRefreshVersion.current++ }, [])

  const loadOntologyGraph = useCallback(async (force = false) => {
    const version = ++graphRefreshVersion.current
    const started = performance.now()
    setGraphLoading(true)
    setGraphError(undefined)
    try {
      if (!userId) throw new Error('Sign in to Fabric before querying the materialized native Ontology graph.')
      const { ontology: contract, ontologyGraph: next } = await loadNativeGraphSnapshot(
        () => queryOntologyGraph(force), queryOntologyContract,
      )
      if (version !== graphRefreshVersion.current) return
      setOntology(contract)
      setOntologyGraph(next)
      setLoadedUserId(userId)
      setGraphQueriedAt(next ? Date.now() : undefined)
      setGraphLoadMs(performance.now() - started)
      setGraphError(next ? undefined : 'No verified native graph was returned. Check Manage graph materialization, source eligibility and the explicit graph binding.')
    } catch (error) {
      if (version !== graphRefreshVersion.current) return
      console.warn('Ontology graph query failed; no partial or fallback topology is displayed.', error)
      setOntologyGraph(null)
      setGraphQueriedAt(undefined)
      setGraphError(error instanceof Error ? error.message : 'Ontology graph query failed. Refresh and check Fabric access.')
    }
    finally {
      if (version === graphRefreshVersion.current) setGraphLoading(false)
    }
  }, [userId])

  const refreshAll = async () => {
    const authorizationVersion = ++graphRefreshVersion.current
    setGraphLoading(true)
    try {
      await connectOntologyAccess()
      if (authorizationVersion !== graphRefreshVersion.current) return
      await loadOntologyGraph(true)
    } catch (error) {
      if (authorizationVersion !== graphRefreshVersion.current) return
      setOntologyGraph(null)
      setGraphQueriedAt(undefined)
      setGraphError(error instanceof Error ? error.message : 'Fabric graph authorization failed. Connect and retry.')
      setGraphLoading(false)
      console.warn('Fabric ontology authorization failed; graph data is unavailable.', error)
    }
  }

  useEffect(() => {
    let inFlight = false
    let pendingMutation = false
    let disposed = false
    const refresh = async (force = false) => {
      pendingMutation ||= force
      if (disposed || inFlight || document.hidden) return
      const refreshAfterMutation = pendingMutation
      pendingMutation = false
      inFlight = true
      try { await loadOntologyGraph(refreshAfterMutation) }
      finally {
        inFlight = false
        if (pendingMutation && !disposed) void refresh()
      }
    }
    const unsubscribe = subscribeWorkOrdersChanged(() => {
      cancelGraphQuery()
      void refresh(true)
    })
    void refresh()
    const interval = window.setInterval(() => void refresh(), GRAPH_REFRESH_POLL_MS)
    const handleVisibility = () => { if (!document.hidden) void refresh() }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      disposed = true
      unsubscribe()
      cancelGraphQuery()
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [cancelGraphQuery, loadOntologyGraph])

  useEffect(() => {
    let resizeFrame = 0
    const layoutFrame = window.requestAnimationFrame(() => {
      resizeFrame = window.requestAnimationFrame(() => {
        controllerRef.current?.resize()
      })
    })
    return () => {
      window.cancelAnimationFrame(layoutFrame)
      window.cancelAnimationFrame(resizeFrame)
    }
  }, [graphView.expanded])

  const graph = useMemo(() => buildKnowledgeGraph({
    facilities: data.stid?.facilities ?? [],
    systems: data.stid?.systems ?? [],
    equipment: data.stid?.equipment ?? [],
    instruments: data.stid?.instruments ?? [],
    telemetry: data.telemetry,
    workOrders: data.orders,
    inspections: data.inspections,
    notifications: data.notifications,
    models: data.assetModels,
    ontology: ontology ?? data.ontology,
    ontologyGraph,
  }), [data.assetModels, data.inspections, data.notifications, data.ontology, data.orders, data.stid, data.telemetry, ontology, ontologyGraph])

  const sharedSelectedId = data.selectedAssetId ? `equipment:${data.selectedAssetId}` : undefined
  const explicitSelectedId = selection && graph.nodes.some(item => item.id === selection.nodeId)
    && (selection.assetId === data.selectedAssetId || graph.nodes.find(item => item.id === selection.nodeId)?.equipmentId === data.selectedAssetId)
    ? selection.nodeId
    : undefined
  const defaultSelectedId = graph.nodes.some(item => item.id === sharedSelectedId)
    ? sharedSelectedId
    : graph.nodes.find(item => item.type === 'equipment')?.id ?? graph.nodes[0]?.id
  const effectiveSelectedId = knowledgeGraphFocusId(scope, explicitSelectedId, defaultSelectedId)
  const scopeAnchorId = effectiveSelectedId ?? defaultSelectedId

  const treeStations = useMemo(() => buildDigitalTwinTree({
    facilities: graph.nodes.filter(node => node.type === 'facility').map(node => ({ facility_id: node.entityId, facility_name: node.label })),
    equipment: graph.nodes.filter(node => node.type === 'equipment' && node.facilityId).map(node => ({
      equipment_id: node.entityId, facility_id: node.facilityId!, system_id: '', tag: node.label,
    })),
  }), [graph.nodes])
  const selectedAssetId = graph.nodes.find(node => node.id === scopeAnchorId)?.equipmentId ?? data.selectedAssetId
  const revealPath = useMemo(() => pathToAsset(treeStations, selectedAssetId), [selectedAssetId, treeStations])
  const expansion = useTreeExpansion(revealPath)
  const assetStatuses = useMemo(() => new Map(graph.nodes.filter(node => node.type === 'equipment').map(node => [node.entityId, node.status])), [graph.nodes])
  const scopeIds = useMemo(() => knowledgeGraphScope(graph, scope, scopeAnchorId, selectedAssetId), [graph, scope, scopeAnchorId, selectedAssetId])
  const visibleNodes = useMemo(() => graph.nodes.filter(node => {
    const matchesQuery = matchesKnowledgeNodeQuery(node, deferredQuery)
    const matchesScope = Boolean(deferredQuery) || !scopeIds || scopeIds.has(node.id)
    return types.has(node.type) && statuses.has(node.status) && matchesQuery && matchesScope
  }), [deferredQuery, graph.nodes, scopeIds, statuses, types])
  const visibleIds = useMemo(() => new Set(visibleNodes.map(item => item.id)), [visibleNodes])
  const visibleEdges = useMemo(() => graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)), [graph.edges, visibleIds])
  const visibleTopology = useMemo(() => summarizeKnowledgeGraph({ nodes: visibleNodes, edges: visibleEdges }), [visibleNodes, visibleEdges])
  const selectedNode = graph.nodes.find(item => item.id === effectiveSelectedId)
  const selectedReadingState = knowledgeTelemetryState(selectedNode?.reading?.eventTime, data.now)

  const selectNode = (nodeId: string) => {
    const node = graph.nodes.find(item => item.id === nodeId)
    setSelection({ nodeId, assetId: data.selectedAssetId })
    if (node?.facilityId && node.equipmentId) data.actions.selectAsset(node.facilityId, node.equipmentId)
  }
  const selectAsset = (facilityId: string, assetId: string) => {
    const node = graph.nodes.find(item => item.type === 'equipment' && item.entityId === assetId)
    data.actions.selectAsset(facilityId, assetId)
    setScope('asset')
    if (node) setSelection({ nodeId: node.id, assetId })
  }
  const selectScope = (nextScope: GraphScope) => {
    setScope(nextScope)
    setSelection(nextScope === 'asset' && defaultSelectedId
      ? { nodeId: defaultSelectedId, assetId: data.selectedAssetId }
      : undefined)
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
  const exportNativeGraph = () => {
    setExportError(undefined)
    if (!ontology || !ontologyGraph) {
      setExportError('Export requires a verified ontology and complete graph result.')
      return
    }
    try {
      const content = serializeNativeKnowledgeGraph(ontology, ontologyGraph, exportFormat)
      const url = URL.createObjectURL(new Blob([content], { type: 'text/turtle;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url
      link.download = knowledgeGraphExportFileName(ontology, exportFormat)
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      setExportError(error instanceof Error ? error.message : 'Knowledge Graph export failed.')
    }
  }
  const zoomGraph = (factor: number) => {
    const controller = controllerRef.current
    if (controller) controller.zoom(controller.zoom() * factor)
  }
  const fitGraph = () => controllerRef.current?.fit(undefined, 48)
  const focusGraph = () => {
    if (!effectiveSelectedId) return
    const controller = controllerRef.current
    if (controller) controller.animate({ center: { eles: controller.getElementById(effectiveSelectedId) }, zoom: 1.25 }, { duration: 300 })
  }

  const hasVerifiedGraph = !graph.error && graph.nodes.length > 0
  const counts = NODE_TYPES.map(item => ({ ...item, count: graph.nodes.filter(node => node.type === item.type).length }))
  const connectedEdges = selectedNode ? graph.edges.filter(item => item.source === selectedNode.id || item.target === selectedNode.id) : []
  const selectedProperties = selectedNode
    ? Object.entries(selectedNode.properties).filter(([, value]) => value !== undefined && value !== '')
    : []
  const primaryProperties = selectedProperties.slice(0, 6)
  const additionalProperties = selectedProperties.slice(6)
  const graphInstanceSummary = hasVerifiedGraph ? `${graph.nodes.length} materialized instances · ${graph.edges.length} native links` : 'No verified graph loaded'
  const graphState = graphLoading ? 'loading' : hasVerifiedGraph ? 'ready' : 'unavailable'
  const graphStatus = graphLoading
    ? hasVerifiedGraph ? 'Refreshing · showing last verified graph' : 'Reading verified graph…'
    : hasVerifiedGraph ? `${ontologyGraph?.graphModelName} · GQL` : 'Native graph unavailable'

  return <div className={`kg-page${graphView.expanded ? ' kg-page-maximized' : ''}`}>
    <header className="kg-header">
      <div><span className="v2-eyebrow">Fabric Ontology</span><h1>Operational Knowledge Graph</h1><p>Explore governed topology, bound time-series state, and maintenance context as one semantic network.</p></div>
      <div className="kg-source-state" data-state={graphState}><span className="kg-live-dot" /><span role="status">{graphStatus}</span><strong title={`${graphInstanceSummary}${hasVerifiedGraph && graphLoadMs !== undefined ? ` · loaded in ${(graphLoadMs / 1000).toFixed(2)}s` : ''}`}>{hasVerifiedGraph ? `${graph.nodes.length} instances · ${graph.edges.length} native links` : 'Awaiting materialized topology'}{hasVerifiedGraph && graphQueriedAt ? ` · verified ${new Date(graphQueriedAt).toLocaleTimeString()}` : ''}</strong><button type="button" disabled={graphLoading} onClick={() => void refreshAll()} aria-label="Refresh Knowledge Graph" title="Refresh graph, telemetry joins, and Ontology contract"><RefreshCw size={14} /></button></div>
    </header>

    <div className={`kg-workspace${graphView.expanded ? ' kg-workspace-maximized' : ''}`}>
      <aside className="kg-sidebar kg-filters">
        <label className="kg-search"><Search size={15} /><input value={query} onChange={event => updateQuery(event.target.value)} placeholder="Find entity, ID, tag, OPC UA node…" /></label>
        <section className="kg-scope-section"><div className="kg-section-title"><span>Graph scope</span><small>{hasVerifiedGraph ? `${visibleNodes.length} visible` : 'Awaiting data'}</small></div><div className="kg-segmented">{([['asset', 'Selected'], ['facility', 'Facility'], ['all', 'All']] as const).map(([value, label]) => <button key={value} className={scope === value ? 'active' : ''} onClick={() => selectScope(value)}>{label}</button>)}</div></section>
        <div className="kg-asset-tree">{hasVerifiedGraph ? <DigitalTwinTree stations={treeStations} selectedAssetId={selectedAssetId} handlers={{ isExpanded: expansion.isExpanded, onToggle: expansion.toggle, onSelectAsset: selectAsset, statusOf: assetId => assetStatuses.get(assetId) ?? 'nodata' }} /> : <p className="kg-awaiting-data">{graphLoading ? 'Asset navigation appears after verification.' : 'No verified asset navigation available.'}</p>}</div>
        <details className="kg-display-filters"><summary>Display filters</summary><section><div className="kg-section-title"><span>Entity classes</span><button onClick={() => setTypes(new Set(NODE_TYPES.map(item => item.type)))}>All</button></div>{counts.map(item => <label className="kg-filter-row" key={item.type}><input type="checkbox" checked={types.has(item.type)} onChange={() => toggleType(item.type)} /><i className={`kg-type-dot type-${item.type}`} /><span>{item.label}</span><small>{item.count}</small></label>)}</section><section><div className="kg-section-title"><span>Operational health</span></div><div className="kg-status-filters">{(['crit', 'warn', 'ok', 'nodata'] as const).map(status => <button key={status} className={statuses.has(status) ? `active status-${status}` : ''} onClick={() => toggleStatus(status)}><i />{STATUS_LABEL[status]}<small>{graph.nodes.filter(item => item.status === status).length}</small></button>)}</div></section></details>
      </aside>

      <section className="kg-stage" aria-label="Knowledge Graph canvas" aria-busy={graphLoading}>
        <div className="kg-toolbar">
          <div className="kg-view-controls"><div className="kg-layout-control"><GitBranch size={14} /><select value={layout} title={LAYOUT_DESCRIPTION[layout]} aria-label={`Graph layout. ${LAYOUT_DESCRIPTION[layout]}`} onChange={event => setLayout(event.target.value as GraphLayout)}><option value="breadthfirst">Hierarchy</option><option value="cose">Semantic network</option><option value="concentric">Concentric</option></select></div></div>
          <div className="kg-graph-actions"><select value={exportFormat} aria-label="Knowledge Graph export format" title="Export the verified native graph schema and instances" onChange={event => setExportFormat(event.target.value as KnowledgeGraphExportFormat)}><option value="owl-turtle">OWL 2 · Turtle</option><option value="rdf-turtle">RDF 1.1 · Turtle</option></select><button type="button" title="Export verified Knowledge Graph" aria-label="Export verified Knowledge Graph" disabled={!hasVerifiedGraph} onClick={exportNativeGraph}><Download size={16} /></button><button type="button" title="Zoom out" aria-label="Zoom out" disabled={!visibleNodes.length} onClick={() => zoomGraph(.8)}><ZoomOut size={16} /></button><button type="button" title="Zoom in" aria-label="Zoom in" disabled={!visibleNodes.length} onClick={() => zoomGraph(1.2)}><ZoomIn size={16} /></button><button type="button" title="Fit graph" aria-label="Fit graph" disabled={!visibleNodes.length} onClick={fitGraph}><Scan size={16} /></button><button type="button" title="Focus selected entity" aria-label="Focus selected entity" disabled={!effectiveSelectedId} onClick={focusGraph}><Focus size={16} /></button><button type="button" title={graphView.expanded ? 'Restore Knowledge Graph' : 'Maximize Knowledge Graph'} aria-label={graphView.expanded ? 'Restore Knowledge Graph' : 'Maximize Knowledge Graph'} aria-pressed={graphView.expanded} onClick={graphView.toggleExpanded}>{graphView.expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button></div>
        </div>
        {exportError && <div className="kg-no-results" role="alert"><p>{exportError}</p></div>}
        {!hasVerifiedGraph
          ? graphLoading
            ? <div className="kg-no-results"><div className="kg-loading-skeleton" aria-hidden="true"><span /><span /><span /></div><h2>Reading verified graph</h2><p>Checking materialized graph readiness. Entities appear after verification.</p></div>
            : <div className="kg-no-results" role="alert"><CircleDot size={32} /><h2>Graph unavailable</h2><p>{graphError ?? graph.error ?? 'Sign in with Fabric item read and execute access, then retry.'}</p><button className="kg-retry" type="button" onClick={() => void refreshAll()}>Connect Fabric and retry</button></div>
          : visibleNodes.length
          ? <KnowledgeGraphCanvas nodes={visibleNodes} edges={visibleEdges} selectedId={effectiveSelectedId} layout={layout} theme={theme} onSelect={selectNode} controllerRef={controllerRef} />
          : <div className="kg-no-results"><CircleDot size={32} /><h2>No matching entities</h2><p>Broaden the entity, health, facility, or search filters.</p></div>}
        <div className="kg-legend"><span><i className="kg-type-dot type-facility" />Facility</span><span><i className="kg-type-dot type-equipment" />Equipment</span><span><i className="kg-type-dot type-instrument" />Instrument</span><span title="Solid arrows are governed relationships, including signal bindings. Arrowheads retain the declared Ontology direction."><i className="kg-relationship-swatch" />Governed relationship</span><span title="Dashed highlights require readings less than 60 seconds old. Motion pauses for reduced motion and offscreen views; large graphs animate the selected neighborhood only. This is not physical process flow."><i className="kg-flow-swatch" />Fresh telemetry path</span><span title="Last-known critical quality or open critical maintenance; stale data does not confirm the current physical condition."><i className="kg-ring ring-crit" />Critical pulse</span><span title="Last-known uncertain quality or open maintenance requiring attention."><i className="kg-ring ring-warn" />Warning pulse</span><span><i className="kg-ring ring-ok" />Healthy ring</span></div>
      </section>

      <aside className="kg-sidebar kg-inspector">
        {selectedNode ? <>
          <div className="kg-inspector-hero">
            <div className="kg-selected-heading"><div className={`kg-selected-node type-${selectedNode.type} status-${selectedNode.status}`}><CircleDot size={20} /></div><div><span className="v2-eyebrow">{selectedNode.type.replace('-', ' ')}</span><h2>{selectedNode.label}</h2><code>{selectedNode.entityId}</code></div></div>
            <div className={`kg-health-banner status-${selectedNode.status}`}><i /><strong>{STATUS_LABEL[selectedNode.status]}</strong><span>{selectedNode.subtitle}</span></div>
          </div>
          {selectedNode.reading && <section className={`kg-live-reading freshness-${selectedReadingState}`}><div><Radio size={15} /><span>{selectedReadingState === 'live' ? 'Live bound signal' : selectedReadingState === 'delayed' ? 'Delayed bound signal' : selectedReadingState === 'stale' ? 'Stale bound signal' : 'Unverified signal timestamp'}</span><small>{new Date(selectedNode.reading.eventTime).toLocaleString()}</small></div><strong>{selectedNode.reading.value.toLocaleString()} <small>{String(selectedNode.properties.Unit ?? '')}</small></strong><p><span>Quality (last known)</span><b>{selectedNode.reading.quality}</b></p><p><span>Event age</span><b>{selectedReadingState === 'unavailable' ? 'Missing, invalid or future time' : ageLabel(selectedNode.reading.eventTime, data.now)}</b></p></section>}
          <section className="kg-property-section"><div className="kg-section-title"><span>Overview</span><small>{selectedProperties.length} properties</small></div><dl>{primaryProperties.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>{additionalProperties.length > 0 && <details className="kg-more-properties"><summary>Show {additionalProperties.length} more properties</summary><dl>{additionalProperties.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></details>}</section>
          <section><div className="kg-section-title"><span>Connected context</span><small>{connectedEdges.length} links</small></div><div className="kg-connections">{connectedEdges.slice(0, 10).map(item => { const outgoing = item.source === selectedNode.id; const otherId = outgoing ? item.target : item.source; const other = graph.nodes.find(node => node.id === otherId); return other ? <button key={item.id} onClick={() => selectNode(other.id)} title={`${outgoing ? selectedNode.label : other.label} → ${item.label} → ${outgoing ? other.label : selectedNode.label}`}><i className={`kg-type-dot type-${other.type}`} /><span><strong>{other.label}</strong><small>{outgoing ? 'Outgoing' : 'Incoming'} · {item.label} · {other.type.replace('-', ' ')}</small></span><ChevronRight size={14} /></button> : null })}</div></section>
          <section><div className="kg-section-title"><span>Data lineage</span><ShieldCheck size={14} /></div><div className="kg-lineage"><div><span>Fabric Ontology</span><ChevronRight size={12} /><span>Native graph</span><ChevronRight size={12} /><span>Operational context</span></div><p><Database size={14} />{selectedNode.provenance}</p></div></section>
          {selectedNode.equipmentId && <div className="kg-open-actions"><button onClick={() => openRelatedView('digital-twin')}><Box size={15} />Digital Twin</button><button onClick={() => openRelatedView('telemetry')}><Activity size={15} />Telemetry</button><button onClick={() => openRelatedView('maintenance')}><Wrench size={15} />Maintenance</button></div>}
        </> : <div className="kg-no-selection"><CircleDot size={28} /><h2>{hasVerifiedGraph ? 'Select an entity' : 'Entity details'}</h2><p>{hasVerifiedGraph ? 'Inspect properties, bound values, relationships, and provenance.' : 'Properties and provenance appear after the graph is verified.'}</p></div>}
      </aside>
    </div>
    <details className="kg-source-coverage"><summary>Advanced graph details · {hasVerifiedGraph ? `${graph.nodes.length} instances · ${graph.edges.length} links` : graphLoading ? 'Loading' : 'Unavailable'}</summary>
      {hasVerifiedGraph && <p>Current view: {visibleTopology.components} connected components · {visibleTopology.isolates} isolated entities. Filters can disconnect the visible view; missing relationships are never synthesized.</p>}
      <p>{hasVerifiedGraph && graphLoadMs !== undefined ? `Graph loaded in ${(graphLoadMs / 1000).toFixed(2)}s. ` : ''}Moving dashes require valid telemetry less than 60 seconds old. Delayed or stale paths remain solid; dashes never mean broken relationships. Red and amber pulses retain last-known quality or maintenance alerts, not proof of the current physical condition. Large graphs animate only the selected telemetry neighborhood.</p>
      <p>Topology comes only from the explicitly bound, materialized Ontology graph. SQL and Eventhouse enrich native entities; they do not supply fallback nodes or relationships. Disconnected components reflect returned native topology, not missing visual links to be guessed.</p>
    </details>
  </div>
}