import type { Core } from 'cytoscape'
import { Bell, BookOpen, Box, Building2, CircleDot, CloudSun, Download, Focus, Gauge, GitBranch, Maximize2, Minimize2, Network, Package, Radio, RefreshCw, Scan, Search, ShieldCheck, Tags, Users, Waves, Wrench, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { connectOntologyAccess, queryOntologyContract, type OntologyContract } from '../../services/fabric'
import { KnowledgeGraphCanvas, type GraphLayout } from '../components/knowledgeGraph/KnowledgeGraphCanvas'
import { ontologyNeighborhoodIds, ontologyNodePresentation, ontologyPropertyPresentation, type OntologyPresentationCategory } from '../components/knowledgeGraph/ontologyModelPresentation'
import { buildOntologyModel, matchesKnowledgeNodeQuery, summarizeKnowledgeGraph } from '../knowledgeGraphModel'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useExpandedView } from '../hooks/useExpandedView'
import { useTheme } from '../hooks/useTheme'

const CATEGORY_ICON = {
  facility: Building2, system: Network, equipment: Box, instrument: Gauge, signal: Waves,
  ontology: Box, model: Box, 'work-order': Wrench, inspection: ShieldCheck, notification: Bell,
  weather: CloudSun, document: BookOpen, organization: Users,
  'equipment-type': Tags, part: Package,
} satisfies Record<OntologyPresentationCategory, typeof Box>

const LAYOUT_DESCRIPTION: Record<GraphLayout, string> = {
  breadthfirst: 'Hierarchy groups connected entity types in levels. Arrows always retain the declared direction.',
  cose: 'Network spaces connected entity types using a force-directed layout.',
  concentric: 'Concentric places highly connected entity types at the center.',
}

function EntityIcon({ node }: { node: Parameters<typeof ontologyNodePresentation>[0] }) {
  const presentation = ontologyNodePresentation(node)
  const Icon = CATEGORY_ICON[presentation.category]
  return <span className="kg-model-entity-icon" style={{ backgroundColor: presentation.color }}><Icon size={18} aria-hidden="true" /></span>
}

export function OntologyModelView() {
  const { user } = useHydroOperationsData()
  const userId = user?.id
  const { theme } = useTheme()
  const expanded = useExpandedView()
  const controllerRef = useRef<Core | null>(null)
  const version = useRef(0)
  const cancelLoad = useCallback(() => { version.current++ }, [])
  const inFlight = useRef(false)
  const [snapshot, setSnapshot] = useState<{ userId: string; ontology: OntologyContract }>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const [exportError, setExportError] = useState<string>()
  const [queriedAt, setQueriedAt] = useState<number>()
  const [selectedId, setSelectedId] = useState<string>()
  const [query, setQuery] = useState('')
  const [layout, setLayout] = useState<GraphLayout>('cose')
  const [scope, setScope] = useState<'all' | 'neighborhood'>('all')
  const [showRelationshipLabels, setShowRelationshipLabels] = useState(true)
  const [zoom, setZoom] = useState(1)
  const ontology = snapshot?.userId === userId ? snapshot?.ontology ?? null : null
  const model = useMemo(() => buildOntologyModel(ontology), [ontology])
  const summary = useMemo(() => summarizeKnowledgeGraph(model), [model])
  const selected = model.nodes.find(node => node.id === selectedId) ?? model.nodes[0]
  const visibleModel = useMemo(() => {
    if (scope === 'all' || !selected) return model
    const ids = ontologyNeighborhoodIds(model.edges, selected.id)
    return {
      nodes: model.nodes.filter(node => ids.has(node.id)),
      edges: model.edges.filter(edge => edge.source === selected.id || edge.target === selected.id),
    }
  }, [model, scope, selected])
  const categories = useMemo(() => {
    const counts = new Map<OntologyPresentationCategory, ReturnType<typeof ontologyNodePresentation> & { count: number }>()
    for (const node of model.nodes) {
      const presentation = ontologyNodePresentation(node)
      const previous = counts.get(presentation.category)
      counts.set(presentation.category, { ...presentation, count: (previous?.count ?? 0) + 1 })
    }
    return [...counts.values()]
  }, [model])
  const matchingNodes = model.nodes.filter(node => matchesKnowledgeNodeQuery(node, query))
  const entity = ontology?.entityTypes.find(item => item.id === selected?.ontologyEntityTypeId)
  const relationships = ontology?.relationshipTypes.filter(item =>
    item.sourceEntityTypeId === entity?.id || item.targetEntityTypeId === entity?.id) ?? []
  const isolated = model.nodes.filter(node => !model.edges.some(edge => edge.source === node.id || edge.target === node.id))
  const ready = Boolean(ontology && !model.error)

  useEffect(() => {
    const graph = controllerRef.current
    if (!graph) return
    const updateZoom = () => setZoom(graph.zoom())
    const frame = window.requestAnimationFrame(updateZoom)
    graph.on('zoom', updateZoom)
    return () => {
      window.cancelAnimationFrame(frame)
      graph.off('zoom', updateZoom)
    }
  }, [ready, theme])

  const load = useCallback(async (force = true) => {
    const current = ++version.current
    inFlight.current = true
    setLoading(true)
    setError(undefined)
    let timeout: ReturnType<typeof setTimeout> | undefined
    try {
      if (!userId) throw new Error('Sign in with Fabric item read access to read the Ontology definition.')
      const contract = await Promise.race([queryOntologyContract(force), new Promise<never>((_, reject) => {
        timeout = setTimeout(() => reject(new Error('Ontology definition loading exceeded 45 seconds. Check Fabric access and retry.')), 45_000)
      })])
      const graph = buildOntologyModel(contract)
      if (!contract || graph.error) throw new Error(graph.error ?? 'No verified Ontology definition was returned.')
      if (current !== version.current) return
      setSnapshot({ userId, ontology: contract })
      setQueriedAt(Date.now())
    } catch (failure) {
      if (current !== version.current) return
      setSnapshot(undefined)
      setQueriedAt(undefined)
      setError(failure instanceof Error ? failure.message : 'The Ontology definition could not be read.')
      console.warn('Ontology model read failed; no cached or inferred topology is displayed.', failure)
    } finally {
      if (timeout !== undefined) clearTimeout(timeout)
      if (current === version.current) {
        inFlight.current = false
        setLoading(false)
      }
    }
  }, [userId])

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0)
    const refresh = () => { if (!document.hidden && !inFlight.current) void load() }
    const interval = window.setInterval(refresh, 5 * 60_000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      cancelLoad()
      window.clearTimeout(initial)
      window.clearInterval(interval)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [cancelLoad, load])

  const refresh = async () => {
    const authorizationVersion = ++version.current
    setLoading(true)
    try {
      await connectOntologyAccess()
      if (authorizationVersion !== version.current) return
      await load()
    } catch (failure) {
      if (authorizationVersion !== version.current) return
      cancelLoad()
      setSnapshot(undefined)
      setQueriedAt(undefined)
      setLoading(false)
      inFlight.current = false
      setError(failure instanceof Error ? failure.message : 'Fabric ontology authorization failed.')
      console.warn('Fabric ontology authorization failed; the model is unavailable.', failure)
    }
  }
  const select = (id: string) => {
    setSelectedId(id)
    const graph = controllerRef.current
    const node = graph?.getElementById(id)
    if (graph && node?.nonempty()) graph.animate({ center: { eles: node }, zoom: Math.max(graph.zoom(), .8) }, { duration: 200 })
  }
  const fitModel = () => controllerRef.current?.fit(undefined, 72)
  const focusSelected = () => {
    const graph = controllerRef.current
    if (graph && selected) graph.fit(graph.getElementById(selected.id).closedNeighborhood(), 90)
  }
  const zoomModel = (factor: number) => {
    const graph = controllerRef.current
    if (graph) graph.zoom({
      level: Math.min(graph.maxZoom(), Math.max(graph.minZoom(), graph.zoom() * factor)),
      renderedPosition: { x: graph.width() / 2, y: graph.height() / 2 },
    })
  }
  const exportModel = () => {
    setExportError(undefined)
    try {
      if (!ontology || !ready) throw new Error('Export requires a verified Ontology model.')
      const url = URL.createObjectURL(new Blob([JSON.stringify({ kind: 'ontology-schema', ontology }, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `${ontology.id}-ontology-model.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (failure) {
      setExportError(failure instanceof Error ? failure.message : 'Ontology schema export failed.')
    }
  }

  return <div className={`kg-page kg-ontology-model${expanded.expanded ? ' kg-page-maximized' : ''}`}>
    <header className="kg-header">
      <div><span className="v2-eyebrow">Live Fabric Ontology</span><h1>Ontology Knowledge Model</h1><p>Entity types and declared relationships. No graph materialization or asset-instance reconstruction.</p></div>
      <div className="kg-source-state" data-state={loading ? 'loading' : ready ? 'ready' : 'unavailable'}>
        <span className="kg-live-dot" /><span role="status">{loading ? 'Reading Ontology definition...' : ready ? 'Verified live Ontology model' : 'Ontology model unavailable'}</span>
        <strong>{ready ? `${model.nodes.length} entity types · ${model.edges.length} declared relationships` : 'Awaiting verified definition'}{ready && queriedAt ? ` · ${new Date(queriedAt).toLocaleTimeString()}` : ''}</strong>
        <button type="button" disabled={loading} onClick={() => void refresh()} aria-label="Refresh Ontology model"><RefreshCw size={14} /></button>
      </div>
    </header>
    <div className={`kg-workspace${expanded.expanded ? ' kg-workspace-maximized' : ''}`}>
      <aside className="kg-sidebar kg-filters">
        <label className="kg-search"><Search size={15} /><input aria-label="Find entity type" value={query} onChange={event => setQuery(event.target.value)} placeholder="Find an entity type..." /></label>
        <section><div className="kg-section-title"><span>Model scope</span><small>{visibleModel.nodes.length} visible</small></div>
          <div className="kg-segmented" role="group" aria-label="Ontology model scope">
            <button type="button" className={scope === 'all' ? 'active' : ''} aria-pressed={scope === 'all'} onClick={() => setScope('all')}>All types</button>
            <button type="button" className={scope === 'neighborhood' ? 'active' : ''} aria-pressed={scope === 'neighborhood'} disabled={!selected} onClick={() => setScope('neighborhood')}>Neighborhood</button>
          </div>
          <p>{scope === 'all' ? 'Selection highlights only directly declared neighbors.' : 'Selected type and its directly declared relationships only.'}</p>
        </section>
        <section><div className="kg-section-title"><span>Business categories</span><small>Legend</small></div>
          <div className="kg-model-category-legend">{categories.map(item => <span key={item.category}><i className="kg-type-dot" style={{ backgroundColor: item.color }} />{item.label}<small>{item.count}</small></span>)}</div>
          <p>Colors and icons describe entity types, not asset health or relationship ownership.</p>
        </section>
        <section><div className="kg-section-title"><span>Entity types</span><small>{matchingNodes.length} / {model.nodes.length}</small></div>
          <div className="kg-model-entities">{matchingNodes.map(node =>
            <button type="button" key={node.id} aria-pressed={selected?.id === node.id} onClick={() => select(node.id)}><EntityIcon node={node} /><span><strong>{node.label}</strong><small>{ontologyNodePresentation(node).label} · {node.properties['Declared relationships']} links</small></span></button>)}</div>
          {ready && !matchingNodes.length && <p>No entity types match. Clear the search to browse all types.</p>}
          {query && <p>Search filters this list, not the diagram or its relationships.</p>}
        </section>
        {ready && <section><div className="kg-section-title"><span>Topology</span></div><p>{summary.components} connected components</p>
          <details><summary>No declared relationships ({isolated.length})</summary><div className="kg-model-entities">{isolated.map(node =>
            <button type="button" key={node.id} onClick={() => select(node.id)}><EntityIcon node={node} /><span>{node.label}</span></button>)}</div></details>
          <p>Unrelated types remain visible, grouped separately. No connections are invented.</p>
        </section>}
      </aside>
      <section className="kg-stage" aria-label="Ontology model canvas" aria-busy={loading}>
        <div className="kg-toolbar">
          <div className="kg-layout-control"><GitBranch size={16} /><select aria-label="Ontology model layout" title={LAYOUT_DESCRIPTION[layout]} value={layout} onChange={event => setLayout(event.target.value as GraphLayout)}>
            <option value="breadthfirst">Hierarchy</option><option value="cose">Semantic network</option><option value="concentric">Concentric</option>
          </select></div>
          <div className="kg-graph-actions">
            <button type="button" title="Export verified Ontology schema JSON" aria-label="Export Ontology schema JSON" disabled={!ready} onClick={exportModel}><Download size={16} /></button>
            <button type="button" title="Zoom out" aria-label="Zoom out Ontology model" disabled={!ready} onClick={() => zoomModel(.8)}><ZoomOut size={16} /></button>
            <output className="kg-model-zoom" aria-label="Ontology model zoom">{Math.round(zoom * 100)}%</output>
            <button type="button" title="Zoom in" aria-label="Zoom in Ontology model" disabled={!ready} onClick={() => zoomModel(1.25)}><ZoomIn size={16} /></button>
            <button type="button" title="Fit all visible entity types" aria-label="Fit Ontology model" disabled={!ready} onClick={fitModel}><Scan size={16} /></button>
            <button type="button" title="Fit selected entity type and declared neighbors" aria-label="Focus selected entity type" disabled={!selected} onClick={focusSelected}><Focus size={16} /></button>
            <button type="button" title={expanded.expanded ? 'Restore view (Escape)' : 'Expand Ontology model'} aria-label={expanded.expanded ? 'Restore Ontology model' : 'Maximize Ontology model'} aria-pressed={expanded.expanded} onClick={expanded.toggleExpanded}>{expanded.expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
          </div>
        </div>
        {ready && <div className="kg-model-canvas-info">
          <span><GitBranch size={13} />{visibleModel.nodes.length} types · {visibleModel.edges.length} declared links</span>
          <label><input type="checkbox" checked={showRelationshipLabels} onChange={event => setShowRelationshipLabels(event.target.checked)} />Relationship labels</label>
        </div>}
        {exportError && <div className="kg-no-results" role="alert"><p>{exportError}</p></div>}
        {ready ? <KnowledgeGraphCanvas modelView showRelationshipLabels={showRelationshipLabels} nodes={visibleModel.nodes} edges={visibleModel.edges} selectedId={selected?.id} layout={layout} theme={theme} onSelect={setSelectedId} controllerRef={controllerRef} />
          : <div className="kg-no-results" role={loading ? 'status' : 'alert'}><CircleDot size={32} /><h2>{loading ? 'Reading Ontology model' : 'Ontology model unavailable'}</h2><p>{loading ? 'Reading the selected generation-2 definition and validating every declared relationship endpoint.' : error ?? model.error}</p>{!loading && <button className="kg-retry" type="button" onClick={() => void refresh()}>Connect Fabric and retry</button>}</div>}
        <div className="kg-legend"><span><i className="kg-relationship-swatch" />Declared direction</span><span>Drag to pan · Scroll to zoom · Click a type to inspect</span><span>Schema only - not individual turbines, readings or work orders</span></div>
      </section>
      <aside className="kg-sidebar kg-inspector">
        {entity && selected ? <>
          <div className="kg-inspector-hero">
            <div className="kg-model-selected-heading"><EntityIcon node={selected} /><div><span className="v2-eyebrow">Ontology entity type</span><h2>{selected.label}</h2></div></div>
            <p>{entity.name}</p>
            <div className="kg-model-definition-badge"><ShieldCheck size={14} />Verified generation-2 definition</div>
            <p>Schema, not an asset instance. No telemetry or health is assigned.</p>
          </div>
          <section><div className="kg-section-title"><span>Declared neighborhood</span><button type="button" onClick={focusSelected}>Focus</button></div>
            <div className="kg-model-neighborhood-counts"><span><strong>{relationships.filter(item => item.targetEntityTypeId === entity.id).length}</strong> incoming</span><span><strong>{relationships.filter(item => item.sourceEntityTypeId === entity.id).length}</strong> outgoing</span></div>
          </section>
          <section><div className="kg-section-title"><span>Definition</span></div><dl className="kg-property-list">{Object.entries(selected.properties).filter(([, value]) => value !== undefined && value !== '').map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl></section>
          <section><div className="kg-section-title"><span>Declared relationships</span><small>{relationships.length}</small></div>
            {!relationships.length && <p>This entity type has no declared relationships in the selected Ontology.</p>}
            {relationships.map(relationship => {
              const edge = model.edges.find(item => item.ontologyRelationshipId === relationship.id)!
              const source = model.nodes.find(node => node.id === edge.source)!
              const target = model.nodes.find(node => node.id === edge.target)!
              const other = source.id === selected.id ? target : source
              return <button className="kg-model-relationship" key={relationship.id} type="button" onClick={() => select(other.id)}>
                <small className="kg-model-relationship-direction"><Radio size={12} />{source.id === selected.id ? 'Outgoing' : 'Incoming'} declaration</small>
                <strong>{edge.label}</strong><span>{source.label} → {target.label}</span>
                <small>{relationship.sourceCardinality ?? 'unspecified'} → {relationship.targetCardinality ?? 'unspecified'}</small>
                <small>{relationship.sourceKeys.join(', ') || 'No source key declared'} → {relationship.targetKeys.join(', ') || 'No target key declared'}</small>
              </button>
            })}
          </section>
          <section><div className="kg-section-title"><span>Properties</span><small>{Object.keys(entity.properties).length}</small></div><dl className="kg-property-list">{ontologyPropertyPresentation(entity).map(property => <div key={property.name}><dt>{property.name}</dt><dd>{property.dataType}{property.lineageId && <details><summary>Lineage</summary><code>{property.lineageId}</code></details>}</dd></div>)}</dl></section>
          <section><div className="kg-section-title"><span>Definition lineage</span></div><p>{selected.provenance}</p><p>Relationships come only from the selected Ontology definition. This view does not certify native graph materialization or agent execution.</p></section>
        </> : <p>Entity definitions and relationships appear after verification.</p>}
      </aside>
    </div>
    {ontology?.warnings?.length ? <details className="kg-source-coverage"><summary>Ontology definition warnings</summary>{ontology.warnings.map(warning => <p key={warning}>{warning}</p>)}</details> : null}
  </div>
}
