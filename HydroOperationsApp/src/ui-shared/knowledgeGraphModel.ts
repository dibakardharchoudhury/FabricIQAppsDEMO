import type { Equipment, Facility, Instrument, OntologyContract, OntologyGraph, System, TelemetryReading } from '../services/fabric'
import type { Asset3DModelRecord, InspectionRecord, MaintenanceNotificationRecord, WorkOrderRecord } from '../services/rayfin'
import { twinStatus, type TwinStatus } from '../twin'

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

/** The native query refreshes the contract cache; read that contract only after it succeeds. */
export async function loadNativeGraphSnapshot(
  queryGraph: () => Promise<OntologyGraph | null>,
  readRefreshedContract: () => Promise<OntologyContract | null>,
): Promise<{ ontologyGraph: OntologyGraph | null; ontology: OntologyContract | null }> {
  const ontologyGraph = await queryGraph()
  if (!ontologyGraph) return { ontologyGraph: null, ontology: null }
  const ontology = await readRefreshedContract()
  if (!ontology || ontology.generation !== 2) throw new Error('The native graph has no verified generation-2 Ontology contract. Refresh discovery and retry.')
  if (ontologyGraph.ontologyId && ontologyGraph.ontologyId !== ontology.id) throw new Error('Ontology selection changed during graph loading. Refresh discovery and retry.')
  return { ontologyGraph, ontology }
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
  ontologyGraph?: OntologyGraph | null
}

const nodeId = (type: KnowledgeNodeType, id: string) => `${type}:${id}`
const edge = (source: string, target: string, type: KnowledgeEdgeType, label: string): KnowledgeEdge => ({
  id: `${source}|${type}|${target}`, source, target, type, label,
  provenance: 'SQL operational overlay · matched native equipment entity',
})
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
}

/** Native relationships, not foreign-key properties, determine presentation context. */
function relatedNodes(node: KnowledgeNode, nodes: KnowledgeNode[], edges: KnowledgeEdge[], target: KnowledgeNodeType, through: KnowledgeNodeType[]): KnowledgeNode[] {
  const byId = new Map(nodes.map(item => [item.id, item]))
  const visited = new Set([node.id])
  const queue = [node.id]
  const found = new Map<string, KnowledgeNode>()
  while (queue.length) {
    const id = queue.shift()!
    for (const relationship of edges) {
      const otherId = relationship.source === id ? relationship.target : relationship.target === id ? relationship.source : undefined
      if (!otherId || visited.has(otherId)) continue
      visited.add(otherId)
      const other = byId.get(otherId)
      if (other?.type === target) found.set(other.id, other)
      else if (other && through.includes(other.type)) queue.push(other.id)
    }
  }
  return [...found.values()]
}

export function knowledgeGraphScope(graph: KnowledgeGraph, scope: 'asset' | 'facility' | 'all', selectedId?: string): Set<string> | undefined {
  if (scope === 'all') return undefined
  const selected = graph.nodes.find(node => node.id === selectedId)
  if (!selected) return undefined
  if (scope === 'facility') {
    return selected.facilityId ? new Set(graph.nodes.filter(node => node.facilityId === selected.facilityId).map(node => node.id)) : new Set([selected.id])
  }
  const asset = graph.nodes.find(node => node.type === 'equipment' && node.entityId === selected.equipmentId) ?? selected
  const visible = new Set([asset.id, selected.id])
  for (const node of graph.nodes) if (asset.equipmentId && node.equipmentId === asset.equipmentId) visible.add(node.id)
  for (const node of relatedNodes(asset, graph.nodes, graph.edges, 'system', [])) visible.add(node.id)
  for (const node of relatedNodes(asset, graph.nodes, graph.edges, 'facility', ['system'])) visible.add(node.id)
  return visible
}

export function buildKnowledgeGraph(input: KnowledgeGraphInput): KnowledgeGraph {
  const unavailable = (error: string): KnowledgeGraph => ({ nodes: [], edges: [], error })
  const contract = input.ontology
  const native = input.ontologyGraph
  if (!contract || contract.generation !== 2) return unavailable('A verified generation-2 Ontology contract is required. Refresh ontology discovery.')
  if (!native) return unavailable('No verified native backing graph is loaded for the selected Ontology. Materialize it using Manage graph in the Ontology portal, then configure the verified workspace, Ontology, and graph mapping and retry. Lakehouse data cannot replace native topology.')
  if (native.ontologyId && native.ontologyId !== contract.id) return unavailable('The native backing graph belongs to a different Ontology than the current contract. Refresh ontology discovery and retry.')
  if (!native.graphModelId) return unavailable('The native graph result has no verified backing graph identity. Refresh ontology discovery and retry.')
  if (!native.nodes.length) return unavailable('The selected Ontology backing graph contains no entities. Check Manage graph materialization and the verified workspace, Ontology, and graph mapping, then retry.')
  const nodes: KnowledgeNode[] = []
  const edges: KnowledgeEdge[] = []
  const readings = new Map(input.telemetry.map(item => [item.opcuaNodeId, item]))
  const byOid = new Map<string, KnowledgeNode>()
  for (const raw of native.nodes) {
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
    const keyValues = entity?.entityIdParts.map(key => text(properties[key]))
    const contractKey = keyValues?.length && keyValues.every(Boolean) ? keyValues.join('|') : undefined
    const entityId = contractKey ?? (role ? text(properties[role.key]) : undefined)
    const type = role?.type ?? 'ontology'
    const id = entityId && role ? nodeId(type, entityId) : nodeId('ontology', raw.oid)
    if (nodes.some(node => node.id === id)) return unavailable(`Native entities have an ambiguous ${type} identity ${entityId}; cannot safely join operational context.`)
    const opcuaNodeId = (type === 'signal' || type === 'instrument') ? text(properties.opcua_node_id) : undefined
    const reading = opcuaNodeId ? readings.get(opcuaNodeId) : undefined
    const label = text(properties.tag ?? properties.facility_name ?? properties.system_name ?? properties.equipment_name
      ?? Object.entries(properties).find(([key]) => key.endsWith('_name') || key === 'name')?.[1]) ?? entityId ?? raw.oid
    const node: KnowledgeNode = {
      id, entityId: entityId ?? raw.oid, type, label,
      subtitle: reading ? `${reading.value.toLocaleString()} ${text(properties.unit) ?? ''}`.trim() : text(properties.equipment_type_name ?? properties.instrument_type) ?? entity?.name ?? raw.labels.join(', '),
      status: type === 'equipment' ? 'nodata' : type === 'instrument' || type === 'signal' ? twinStatus({ id, label, nodeId: opcuaNodeId ?? '', value: reading?.value, quality: reading?.quality }) : 'ok',
      nativeOid: raw.oid, ontologyEntityTypeId: entity?.id,
      facilityId: type === 'facility' ? entityId : undefined,
      equipmentId: type === 'equipment' ? entityId : undefined,
      properties: {
        ...presentGraphProperties(properties), 'Native OID': raw.oid, 'Native labels': raw.labels.join(', '), 'Ontology entity': entity?.name ?? raw.labels.join(', '),
        ...(opcuaNodeId ? { 'OPC UA node': opcuaNodeId, Unit: text(properties.unit), 'Latest value': reading?.value, Quality: reading?.quality, 'Event time': reading?.eventTime } : {}),
      },
      reading,
      provenance: `Fabric Ontology v2 · ${native.graphModelName} · native materialized graph node${entity ? ` · ${entity.name}` : ' · unrecognized entity type'}${reading ? ' · KQL Eventhouse time-series enrichment via opcua_node_id' : ''}`,
    }
    nodes.push(node)
    byOid.set(raw.oid, node)
  }

  for (const raw of native.edges) {
    const source = byOid.get(raw.sourceOid)
    const target = byOid.get(raw.targetOid)
    if (!source || !target) return unavailable(`Native relationship ${raw.oid} references an entity missing from the graph result. Retry the complete graph query.`)
    if (edges.some(item => item.nativeOid === raw.oid)) return unavailable(`Native graph has duplicate relationship OID ${raw.oid}.`)
    const candidates = contract.relationshipTypes.filter(item => raw.relationshipTypeId
      ? item.id === raw.relationshipTypeId
      : raw.labels.includes(item.id) || raw.labels.includes(item.name))
    if (raw.relationshipTypeId && candidates.length !== 1) return unavailable(`Native relationship ${raw.oid} has a relationship type ID absent or ambiguous in the current Ontology contract.`)
    const matches = candidates.filter(item => item.sourceEntityTypeId === source.ontologyEntityTypeId && item.targetEntityTypeId === target.ontologyEntityTypeId)
    if (candidates.length && matches.length !== 1) return unavailable(`Native relationship ${raw.oid} does not unambiguously match the Ontology contract's directed endpoints.`)
    const relationship = matches[0]
    const name = relationship?.name ?? raw.labels.join(', ') ?? 'RELATED TO'
    const type = source.type === 'signal' || target.type === 'signal' ? 'has-signal' : source.type === 'instrument' || target.type === 'instrument' ? 'has-instrument' : 'contains'
    edges.push({
      id: `native-edge:${raw.oid}`, nativeOid: raw.oid, source: source.id, target: target.id, type,
      label: (relationship?.label ?? (name || 'RELATED TO')).replaceAll('_', ' ').toUpperCase(),
      ontologyRelationshipId: relationship?.id, ontologyRelationshipName: relationship?.name,
      provenance: `Native relationship ${raw.oid} · labels: ${raw.labels.join(', ')}${relationship ? ` · Ontology ${relationship.name} (${relationship.id})` : ' · relationship type not identified in contract'}`,
    })
  }

  for (const node of nodes) {
    if (node.type !== 'facility') {
      const facilities = relatedNodes(node, nodes, edges, 'facility', ['system', ...(node.type === 'equipment' ? [] : ['equipment' as const, 'instrument' as const, 'signal' as const])])
      if (facilities.length === 1) node.facilityId = facilities[0].entityId
    }
    if (!['equipment', 'facility', 'system'].includes(node.type)) {
      const equipment = relatedNodes(node, nodes, edges, 'equipment', ['instrument', 'signal'])
      if (equipment.length === 1) node.equipmentId = equipment[0].entityId
    }
  }
  const equipment = new Map(nodes.filter(node => node.type === 'equipment' && node.equipmentId).map(node => [node.entityId, node]))
  const orders = input.workOrders.filter(order => equipment.has(order.equipmentId))
  const openNodeIds = new Set(orders.filter(order => !['completed', 'cancelled'].includes(order.status.toLowerCase())).map(order => `${order.equipmentId}|${order.opcuaNodeId}`))
  for (const node of nodes) {
    const opcuaNodeId = node.properties['OPC UA node']
    if (opcuaNodeId && node.equipmentId && openNodeIds.has(`${node.equipmentId}|${opcuaNodeId}`)) {
      node.status = twinStatus({ id: node.id, label: node.label, nodeId: String(opcuaNodeId), value: node.reading?.value, quality: node.reading?.quality, hasOpenIssue: true })
    }
  }
  for (const asset of equipment.values()) {
    const statuses = nodes.filter(node => ['instrument', 'signal'].includes(node.type) && node.equipmentId === asset.entityId).map(node => node.status)
    asset.status = statuses.includes('crit') ? 'crit' : statuses.includes('warn') ? 'warn' : statuses.includes('ok') ? 'ok' : 'nodata'
  }

  for (const model of input.models.filter(item => equipment.has(item.equipmentId))) {
    nodes.push({ id: nodeId('model', model.id), entityId: model.id, type: 'model', label: model.modelName, subtitle: `${model.format}${model.version ? ` · ${model.version}` : ''}`, status: 'ok', equipmentId: model.equipmentId, properties: { Format: model.format, Version: model.version, URL: model.modelUrl, 'File size MB': model.fileSizeMb }, provenance: 'SQL operational enrichment · Asset3DModel' })
    edges.push(edge(equipment.get(model.equipmentId)!.id, nodeId('model', model.id), 'has-model', 'HAS MODEL'))
  }
  for (const order of orders) {
    const closed = ['completed', 'cancelled'].includes(order.status.toLowerCase())
    nodes.push({ id: nodeId('work-order', order.id), entityId: order.workOrderNumber, type: 'work-order', label: order.workOrderNumber, subtitle: order.title, status: closed ? 'ok' : order.priority.toLowerCase() === 'critical' ? 'crit' : 'warn', equipmentId: order.equipmentId, properties: { Title: order.title, Priority: order.priority, Status: order.status, Created: String(order.createdAt), Due: order.dueAt ? String(order.dueAt) : undefined }, provenance: 'SQL operational enrichment · WorkOrder' })
    edges.push(edge(nodeId('work-order', order.id), equipment.get(order.equipmentId)!.id, 'affects', 'AFFECTS'))
  }
  for (const inspection of input.inspections.filter(item => equipment.has(item.equipmentId))) {
    nodes.push({ id: nodeId('inspection', inspection.id), entityId: inspection.id, type: 'inspection', label: inspection.inspectionType, subtitle: inspection.result, status: /fail|issue|attention/i.test(inspection.result) ? 'warn' : 'ok', equipmentId: inspection.equipmentId, properties: { Result: inspection.result, Findings: inspection.findings, Inspected: String(inspection.inspectedAt), 'Next due': inspection.nextDueAt ? String(inspection.nextDueAt) : undefined }, provenance: 'SQL operational enrichment · Inspection' })
    edges.push(edge(nodeId('inspection', inspection.id), equipment.get(inspection.equipmentId)!.id, 'documents', 'DOCUMENTS'))
  }
  for (const notification of input.notifications.filter(item => equipment.has(item.equipmentId))) {
    nodes.push({ id: nodeId('notification', notification.id), entityId: notification.id, type: 'notification', label: notification.summary, subtitle: `${notification.severity} · ${notification.status}`, status: /critical|high/i.test(notification.severity) ? 'crit' : 'warn', equipmentId: notification.equipmentId, properties: { Severity: notification.severity, Status: notification.status, Reported: String(notification.reportedAt), 'OPC UA node': notification.opcuaNodeId }, provenance: 'SQL operational enrichment · MaintenanceNotification' })
    edges.push(edge(nodeId('notification', notification.id), equipment.get(notification.equipmentId)!.id, 'reports', 'REPORTS'))
  }
  for (const node of nodes) {
    if (!node.nativeOid && node.equipmentId) node.facilityId = equipment.get(node.equipmentId)?.facilityId
  }
  return { nodes, edges }
}
