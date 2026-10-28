import type { Equipment, Facility, Instrument, OntologyContract, OntologyGraph, System, TelemetryReading } from '../services/fabric'
import type { OntologyInstanceGraph } from '../services/ontologySourceGraph'
import type { Asset3DModelRecord, InspectionRecord, MaintenanceNotificationRecord, WorkOrderRecord } from '../services/rayfin'
import { twinSignalStatus, twinStatus, type TwinStatus } from '../twin'

export type KnowledgeNodeType = 'facility' | 'system' | 'equipment' | 'instrument' | 'signal' | 'ontology' | 'model' | 'work-order' | 'inspection' | 'notification'
export type KnowledgeNode = {
  id: string
  entityId: string
  type: KnowledgeNodeType
  label: string
  subtitle: string
  status: TwinStatus
  facilityId?: string
  equipmentId?: string
  nativeOid?: string
  ontologyEntityTypeId?: string
  properties: Record<string, string | number | boolean | undefined>
  provenance: string
  reading?: TelemetryReading
}
export type KnowledgeEdgeType = 'contains' | 'has-instrument' | 'has-signal' | 'has-model' | 'affects' | 'documents' | 'reports'
export type KnowledgeEdge = {
  id: string
  source: string
  target: string
  type: KnowledgeEdgeType
  label: string
  nativeOid?: string
  ontologyRelationshipId?: string
  ontologyRelationshipName?: string
  provenance?: string
}
export type KnowledgeGraph = { nodes: KnowledgeNode[]; edges: KnowledgeEdge[]; error?: string }

export function summarizeKnowledgeGraph(graph: KnowledgeGraph): { components: number; isolates: number } {
  const neighbours = new Map(graph.nodes.map(node => [node.id, new Set<string>()]))
  for (const edge of graph.edges) {
    const source = neighbours.get(edge.source)
    const target = neighbours.get(edge.target)
    if (!source || !target) throw new Error('Graph summary received a relationship with a missing endpoint.')
    source.add(edge.target)
    target.add(edge.source)
  }
  const remaining = new Set(neighbours.keys())
  let components = 0
  for (const id of neighbours.keys()) {
    if (!remaining.delete(id)) continue
    components++
    const queue = [id]
    for (let index = 0; index < queue.length; index++) {
      for (const next of neighbours.get(queue[index])!) if (remaining.delete(next)) queue.push(next)
    }
  }
  return { components, isolates: [...neighbours.values()].filter(adjacent => !adjacent.size).length }
}

/** Read the contract used by the native query only after that query succeeds. */
// Allow the native API's 120-second query budget plus graph/ontology discovery.
export const NATIVE_GRAPH_LOAD_TIMEOUT_MS = 150_000

export async function loadNativeGraphSnapshot<T extends OntologyInstanceGraph = OntologyGraph>(
  queryGraph: () => Promise<T | null>,
  readRefreshedContract: () => Promise<OntologyContract | null>,
  timeoutMs = NATIVE_GRAPH_LOAD_TIMEOUT_MS,
): Promise<{ ontologyGraph: T | null; ontology: OntologyContract | null }> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined
  const load = async () => {
    const ontologyGraph = await queryGraph()
    if (!ontologyGraph) return { ontologyGraph: null, ontology: null }
    if (ontologyGraph.execution === 'source-backed') throw new Error('Source-reconstructed topology cannot replace the materialized native Ontology graph.')
    const ontology = await readRefreshedContract()
    if (!ontology || ontology.generation !== 2) throw new Error('The native graph has no verified generation-2 Ontology contract. Refresh discovery and retry.')
    if (ontologyGraph.ontologyId && ontologyGraph.ontologyId !== ontology.id) throw new Error('Ontology selection changed during graph loading. Refresh discovery and retry.')
    return { ontologyGraph, ontology }
  }
  try {
    return await Promise.race([load(), new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => reject(new Error(
        `Ontology graph loading exceeded ${timeoutMs / 1000} seconds. Retry after checking the selected execution source.`,
      )), timeoutMs)
    })])
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId)
  }
}

const normalizeSearch = (value: unknown) => String(value ?? '').trim().toLowerCase()

export function matchesKnowledgeNodeQuery(node: KnowledgeNode, query: string): boolean {
  const normalized = normalizeSearch(query)
  if (!normalized) return true
  return [node.label, node.entityId, node.subtitle, ...Object.values(node.properties)]
    .some(value => normalizeSearch(value).includes(normalized))
}

export function isExactKnowledgeNodeMatch(node: KnowledgeNode, query: string): boolean {
  const normalized = normalizeSearch(query)
  return Boolean(normalized) && [node.label, node.entityId].some(value => normalizeSearch(value) === normalized)
}

export type KnowledgeGraphInput = {
  facilities: Facility[]
  systems: System[]
  equipment: Equipment[]
  instruments: Instrument[]
  telemetry: TelemetryReading[]
  workOrders: WorkOrderRecord[]
  inspections: InspectionRecord[]
  notifications: MaintenanceNotificationRecord[]
  models: Asset3DModelRecord[]
  ontology?: OntologyContract | null
  ontologyGraph?: OntologyInstanceGraph | null
}

const nodeId = (type: KnowledgeNodeType, id: string) => `${type}:${id}`
const presentGraphProperties = (properties: Record<string, unknown>): KnowledgeNode['properties'] => Object.fromEntries(
  Object.entries(properties).map(([key, value]) => [key, typeof value === 'object' && value !== null ? JSON.stringify(value) : value]),
) as KnowledgeNode['properties']
const text = (value: unknown) => value === undefined || value === null ? undefined : String(value)
const roles: Record<string, { type: KnowledgeNodeType; key: string }> = {
  facilities: { type: 'facility', key: 'facility_id' },
  systems: { type: 'system', key: 'system_id' },
  equipment: { type: 'equipment', key: 'equipment_id' },
  instruments: { type: 'instrument', key: 'instrument_id' },
  signal_master: { type: 'signal', key: 'opcua_node_id' },
  WorkOrder: { type: 'work-order', key: 'id' },
  Inspection: { type: 'inspection', key: 'id' },
  Asset3DModel: { type: 'model', key: 'id' },
  MaintenanceNotification: { type: 'notification', key: 'id' },
}

function topologyIndex(nodes: KnowledgeNode[], edges: KnowledgeEdge[]) {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const neighbours = new Map<string, string[]>()
  for (const relationship of edges) for (const [from, to] of [[relationship.source, relationship.target], [relationship.target, relationship.source]]) {
    const adjacent = neighbours.get(from) ?? []
    adjacent.push(to)
    neighbours.set(from, adjacent)
  }
  return { byId, neighbours }
}

function relatedNodes(node: KnowledgeNode, nodes: KnowledgeNode[], edges: KnowledgeEdge[], target: KnowledgeNodeType, through: KnowledgeNodeType[], index = topologyIndex(nodes, edges)): KnowledgeNode[] {
  const { byId, neighbours } = index
  const visited = new Set([node.id])
  const queue = [node.id]
  const found = new Map<string, KnowledgeNode>()
  for (let position = 0; position < queue.length; position++) {
    const id = queue[position]
    for (const otherId of neighbours.get(id) ?? []) {
      if (visited.has(otherId)) continue
      visited.add(otherId)
      const other = byId.get(otherId)
      if (other?.type === target) found.set(other.id, other)
      else if (other && through.includes(other.type)) queue.push(other.id)
    }
  }
  return [...found.values()]
}

export function knowledgeGraphScope(graph: KnowledgeGraph, scope: 'asset' | 'facility' | 'all', selectedId?: string, selectedAssetId?: string): Set<string> | undefined {
  if (scope === 'all') return undefined
  const selected = graph.nodes.find(node => node.id === selectedId)
  if (!selected) return undefined
  const asset = graph.nodes.find(node => node.type === 'equipment' && node.entityId === (selectedAssetId ?? selected.equipmentId)) ?? selected
  if (scope === 'facility') {
    return asset.facilityId ? new Set(graph.nodes.filter(node => node.facilityId === asset.facilityId).map(node => node.id)) : new Set([selected.id])
  }
  const visible = new Set([asset.id])
  for (const node of graph.nodes) if (asset.equipmentId && node.equipmentId === asset.equipmentId) visible.add(node.id)
  for (const node of relatedNodes(asset, graph.nodes, graph.edges, 'system', [])) visible.add(node.id)
  for (const node of relatedNodes(asset, graph.nodes, graph.edges, 'facility', ['system'])) visible.add(node.id)
  return visible
}

export function knowledgeGraphFocusId(
  scope: 'asset' | 'facility' | 'all',
  explicitSelectedId?: string,
  defaultAssetId?: string,
): string | undefined {
  return explicitSelectedId ?? (scope === 'asset' ? defaultAssetId : undefined)
}

export function buildKnowledgeGraph(input: KnowledgeGraphInput): KnowledgeGraph {
  const unavailable = (error: string): KnowledgeGraph => ({ nodes: [], edges: [], error })
  const contract = input.ontology
  const native = input.ontologyGraph
  if (!contract || contract.generation !== 2) return unavailable('A verified generation-2 Ontology contract is required. Refresh ontology discovery.')
  if (!native) return unavailable('No verified native Ontology graph is loaded. Complete Manage graph materialization in the selected Ontology, verify its graph binding, and retry. Lakehouse rows cannot substitute for native topology.')
  if (native.execution === 'source-backed') return unavailable('Source-reconstructed topology cannot replace the materialized native Ontology graph.')
  if (native?.ontologyId && native.ontologyId !== contract.id) return unavailable('The native backing graph belongs to a different Ontology than the current contract. Refresh ontology discovery and retry.')
  if (!native.graphModelId) return unavailable('The native graph result has no verified backing graph identity. Refresh ontology discovery and retry.')
  if (native && !native.nodes.length) return unavailable('The selected Ontology backing graph contains no entities. Check Manage graph materialization and the verified workspace, Ontology, and graph mapping, then retry.')
  const topology = native
  const nodes: KnowledgeNode[] = []
  const edges: KnowledgeEdge[] = []
  const signalBindings: Array<{ source: KnowledgeNode; target: KnowledgeNode }> = []
  const readings = new Map(input.telemetry.map(item => [item.opcuaNodeId, item]))
  const byOid = new Map<string, KnowledgeNode>()
  const nodeIds = new Set<string>()
  const edgeIds = new Set<string>()
  const provenanceName = native.graphModelName
  for (const raw of topology.nodes) {
    if (byOid.has(raw.oid)) return unavailable(`Native graph has duplicate entity OID ${raw.oid}.`)
    const exact = contract.entityTypes.filter(entity => raw.entityTypeId
      ? entity.id === raw.entityTypeId
      : raw.labels.some(label => label === entity.id || label === entity.name))
    if (raw.entityTypeId && exact.length !== 1) return unavailable(`Native entity ${raw.oid} has an entity type ID absent or ambiguous in the current Ontology contract.`)
    const matches = exact.length ? exact : contract.entityTypes.filter(entity => raw.labels.some(label =>
      label === entity.localName,
    ))
    if (matches.length > 1) return unavailable(`Native entity ${raw.oid} has ambiguous Ontology labels: ${raw.labels.join(', ')}.`)
    const entity = matches[0]
    const sourceRole = entity ? Object.keys(roles).find(name => entity.sourceTable === name || entity.sourceTable === `silver_${name}`) : undefined
    const roleNames = entity ? [entity.localName, entity.name, sourceRole] : []
    const role = roleNames.map(name => name ? roles[name] : undefined).find(Boolean)
    const properties = raw.properties
    const keyValues = entity?.entityIdParts.map(key => {
      const name = Object.entries(entity.propertyMetadata ?? {}).find(([, property]) => property.id === key)?.[0] ?? key
      return text(properties[name])
    })
    const contractKey = keyValues?.length && keyValues.every(Boolean) ? keyValues.join('|') : undefined
    const entityId = contractKey ?? (role ? text(properties[role.key]) : undefined)
    const type = role?.type ?? 'ontology'
    const id = entityId && role ? nodeId(type, entityId) : nodeId('ontology', raw.oid)
    if (nodeIds.has(id)) return unavailable(`Ontology entities have an ambiguous ${type} identity ${entityId}; cannot safely join operational context.`)
    nodeIds.add(id)
    const opcuaNodeId = (type === 'signal' || type === 'instrument') ? text(properties.opcua_node_id) : undefined
    const reading = opcuaNodeId ? readings.get(opcuaNodeId) : undefined
    let label = text(properties.tag ?? properties.facility_name ?? properties.system_name ?? properties.equipment_name
      ?? properties.workOrderNumber ?? properties.modelName ?? properties.inspectionType ?? properties.summary
      ?? Object.entries(properties).find(([key]) => key.endsWith('_name') || key === 'name')?.[1]) ?? entityId ?? raw.oid
    let subtitle = (entity?.localName ?? raw.labels[0] ?? 'Ontology entity').replaceAll('_', ' ')
    let presentation = properties
    let provenance = `Fabric Ontology · ${provenanceName} · ${type === 'ontology' ? `${entity?.localName ?? raw.labels[0] ?? 'Ontology entity'} ` : ''}materialized graph node`
    if (type === 'facility') {
      label = text(properties.facility_name) ?? entityId ?? raw.oid
      subtitle = text(properties.type) ?? 'Facility'
      presentation = { Type: properties.type, Country: properties.country, Commissioned: properties.commissioned_date, Latitude: properties.lat, Longitude: properties.lon }
    } else if (type === 'system') {
      label = text(properties.system_name) ?? entityId ?? raw.oid
      presentation = { 'System ID': entityId, 'OAG RDS code': properties.oag_rds_system_code, 'Equipment count': 0 }
    } else if (type === 'equipment') {
      label = text(properties.tag) ?? entityId ?? raw.oid
      subtitle = text(properties.equipment_type_name ?? properties.equipment_type_code) ?? 'Equipment'
      presentation = { 'Equipment ID': entityId, Type: properties.equipment_type_name, Manufacturer: properties.manufacturer, Model: properties.model, Criticality: properties.criticality, Status: properties.status, Installed: properties.install_date, Active: properties.is_active }
    } else if (type === 'instrument') {
      label = text(properties.tag) ?? entityId ?? raw.oid
      subtitle = reading ? `${reading.value.toLocaleString()} ${text(properties.unit) ?? ''}`.trim() : text(properties.instrument_type) ?? 'Instrument'
      presentation = { 'Instrument ID': entityId, Type: properties.instrument_type, 'OPC UA node': opcuaNodeId, Unit: properties.unit, Active: properties.is_active, 'Latest value': reading?.value, Quality: reading?.quality, 'Event time': reading?.eventTime }
    } else if (type === 'signal') {
      label = `Signal · ${text(properties.tag ?? properties.signal_type ?? properties.instrument_id) ?? entityId ?? raw.oid}`
      subtitle = reading ? `${reading.value.toLocaleString()} ${text(properties.unit) ?? ''}`.trim() : text(properties.signal_type) ?? 'Time-series signal'
      presentation = { ...properties, 'Latest value': reading?.value, Quality: reading?.quality, 'Event time': reading?.eventTime }
      provenance = `Fabric Ontology · ${provenanceName} · signal_master node with Eventhouse time-series binding`
    }
    const node: KnowledgeNode = {
      id, entityId: entityId ?? raw.oid, type, label, subtitle,
      status: type === 'equipment' ? 'nodata' : type === 'instrument' || type === 'signal' ? twinSignalStatus({ id, label, nodeId: opcuaNodeId ?? '', value: reading?.value, quality: reading?.quality }) : 'ok',
      nativeOid: raw.oid, ontologyEntityTypeId: entity?.id,
      facilityId: type === 'facility' ? entityId : undefined,
      equipmentId: type === 'equipment' ? entityId : undefined,
      properties: presentGraphProperties(presentation), reading, provenance,
    }
    if (type === 'work-order') {
      node.subtitle = text(properties.title) ?? 'Work order'
      node.status = /^(completed|cancelled)$/i.test(text(properties.status) ?? '') ? 'ok' : /^critical$/i.test(text(properties.priority) ?? '') ? 'crit' : 'warn'
    } else if (type === 'notification') node.status = /critical|high/i.test(text(properties.severity) ?? '') ? 'crit' : 'warn'
    else if (type === 'inspection') node.status = /fail|issue|attention/i.test(text(properties.result) ?? '') ? 'warn' : 'ok'
    nodes.push(node)
    byOid.set(raw.oid, node)
  }

  for (const raw of topology.edges) {
    const source = byOid.get(raw.sourceOid)
    const target = byOid.get(raw.targetOid)
    if (!source || !target) return unavailable(`Native relationship ${raw.oid} references an entity missing from the graph result. Retry the complete graph query.`)
    if (edgeIds.has(raw.oid)) return unavailable(`Graph has duplicate relationship OID ${raw.oid}.`)
    edgeIds.add(raw.oid)
    const candidates = contract.relationshipTypes.filter(item => raw.relationshipTypeId
      ? item.id === raw.relationshipTypeId
      : raw.labels.includes(item.id) || raw.labels.includes(item.name))
    if (raw.relationshipTypeId && candidates.length !== 1) return unavailable(`Native relationship ${raw.oid} has a relationship type ID absent or ambiguous in the current Ontology contract.`)
    const matches = candidates.filter(item => item.sourceEntityTypeId === source.ontologyEntityTypeId && item.targetEntityTypeId === target.ontologyEntityTypeId)
    if (candidates.length && matches.length !== 1) return unavailable(`Native relationship ${raw.oid} does not unambiguously match the Ontology contract's directed endpoints.`)
    const relationship = matches[0]
    const name = relationship?.name ?? raw.labels.join(', ') ?? 'RELATED TO'
    if (source.type === 'signal' && target.type === 'instrument'
      && (name === 'signals_from_instruments' || relationship?.name.endsWith('#signals_from_instruments'))) {
      signalBindings.push({ source, target })
    }
    const endpoints = [source.type, target.type]
    const type: KnowledgeEdgeType = endpoints.includes('work-order') ? 'affects'
      : endpoints.includes('inspection') ? 'documents'
        : endpoints.includes('notification') ? 'reports'
          : endpoints.includes('model') ? 'has-model'
            : endpoints.includes('signal') ? 'has-signal'
              : endpoints.includes('instrument') ? 'has-instrument' : 'contains'
    edges.push({
      id: `native-edge:${raw.oid}`, nativeOid: raw.oid,
      source: source.id, target: target.id, type,
      label: (relationship?.label ?? (name || 'RELATED TO')).replaceAll('_', ' ').toUpperCase(),
      ontologyRelationshipId: relationship?.id, ontologyRelationshipName: relationship?.name,
      provenance: `Native relationship ${raw.oid} · labels: ${raw.labels.join(', ')}${relationship ? ` · Ontology ${relationship.name} (${relationship.id})` : ' · relationship type not identified in contract'}`,
    })
  }

  const sourceCounts = new Map<string, number>()
  const targetCounts = new Map<string, number>()
  const degrees = new Map<string, number>()
  for (const relationship of edges) for (const id of [relationship.source, relationship.target]) {
    degrees.set(id, (degrees.get(id) ?? 0) + 1)
  }
  for (const { source, target } of signalBindings) {
    sourceCounts.set(source.id, (sourceCounts.get(source.id) ?? 0) + 1)
    targetCounts.set(target.id, (targetCounts.get(target.id) ?? 0) + 1)
  }
  const collapsed = new Set<string>()
  for (const { source, target } of signalBindings) {
    if (sourceCounts.get(source.id) !== 1 || targetCounts.get(target.id) !== 1 || degrees.get(source.id) !== 1) continue
    collapsed.add(source.id)
    target.properties['Signal entity'] = source.entityId
    target.provenance = `Fabric Ontology · ${provenanceName} · combined one-to-one instruments + signal_master node with Eventhouse time-series binding`
  }
  for (let index = nodes.length - 1; index >= 0; index--) if (collapsed.has(nodes[index].id)) nodes.splice(index, 1)
  for (let index = edges.length - 1; index >= 0; index--) {
    const relationship = edges[index]
    if (collapsed.has(relationship.source) || collapsed.has(relationship.target)) edges.splice(index, 1)
  }

  const index = topologyIndex(nodes, edges)
  for (const node of nodes) {
    if (node.type === 'system') {
      const count = relatedNodes(node, nodes, edges, 'equipment', [], index).length
      node.subtitle = `${count} connected assets`
      node.properties['Equipment count'] = count
    }
    if (node.type !== 'facility') {
      const facilities = relatedNodes(node, nodes, edges, 'facility', ['system', ...(node.type === 'equipment' ? [] : ['equipment' as const, 'instrument' as const, 'signal' as const])], index)
      if (facilities.length === 1) node.facilityId = facilities[0].entityId
    }
    if (!['equipment', 'facility', 'system'].includes(node.type)) {
      const equipment = relatedNodes(node, nodes, edges, 'equipment', ['instrument', 'signal'], index)
      if (equipment.length === 1) node.equipmentId = equipment[0].entityId
    }
  }
  const equipment = new Map(nodes.filter(node => node.type === 'equipment' && node.equipmentId).map(node => [node.entityId, node]))
  const healthOrders = input.workOrders.filter(order => equipment.has(order.equipmentId))
  const openNodeIds = new Set(healthOrders.filter(order => !['completed', 'cancelled'].includes(order.status.toLowerCase())).map(order => `${order.equipmentId}|${order.opcuaNodeId}`))
  const criticalOpenNodeIds = new Set(healthOrders.filter(order =>
    !['completed', 'cancelled'].includes(order.status.toLowerCase())
    && order.priority.toLowerCase() === 'critical')
    .map(order => `${order.equipmentId}|${order.opcuaNodeId}`))
  for (const asset of equipment.values()) {
    const statuses = nodes.filter(node => ['instrument', 'signal'].includes(node.type) && node.equipmentId === asset.entityId).map(node => {
      const opcuaNodeId = String(node.properties['OPC UA node'] ?? node.properties.opcua_node_id ?? '')
      return twinStatus({
        id: node.id, label: node.label, nodeId: opcuaNodeId,
        value: node.reading?.value, quality: node.reading?.quality,
        hasOpenIssue: openNodeIds.has(`${asset.entityId}|${opcuaNodeId}`),
        hasCriticalIssue: criticalOpenNodeIds.has(`${asset.entityId}|${opcuaNodeId}`),
      })
    })
    asset.status = statuses.includes('crit') ? 'crit' : statuses.includes('warn') ? 'warn' : statuses.includes('ok') ? 'ok' : 'nodata'
  }

  return { nodes, edges }
}
