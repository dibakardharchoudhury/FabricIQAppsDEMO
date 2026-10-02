import type { Equipment, Facility, Instrument, OntologyContract, OntologyGraph, System, TelemetryReading } from '../services/fabric'
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

/** Read the contract used by the native query only after that query succeeds. */
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

function boundOntologyEntities(input: KnowledgeGraphInput, contract: OntologyContract) {
  const nodes: OntologyGraph['nodes'] = []
  const edges: OntologyGraph['edges'] = []
  const tables = { facilities: input.facilities, systems: input.systems, equipment: input.equipment, instruments: input.instruments }
  for (const entity of contract.entityTypes) {
    const table = Object.entries(tables).find(([name]) => entity.sourceTable === name || entity.sourceTable === `silver_${name}`)
    if (!table) continue
    for (const row of table[1]) {
      const properties: Record<string, unknown> = { ...row }
      for (const [name, metadata] of Object.entries(entity.propertyMetadata ?? {})) {
        const column = metadata.backingConfiguration?.valueColumn
        if (typeof column === 'string') {
          const field = column.slice(column.lastIndexOf('.') + 1)
          if (field in row) properties[name] = properties[field]
        }
      }
      const keys = entity.entityIdParts.map(key =>
        Object.entries(entity.propertyMetadata ?? {}).find(([, property]) => property.id === key)?.[0] ?? key)
      const values = keys.map(key => properties[key])
      if (!values.length || values.some(value => value === undefined || value === null || value === '')) {
        return { nodes: [], edges: [], error: `Lakehouse entity ${entity.name} lacks its Ontology key properties.` }
      }
      nodes.push({ oid: `${entity.id}:${JSON.stringify(values)}`, entityTypeId: entity.id, labels: [entity.name], properties })
    }
  }
  for (const relationship of contract.relationshipTypes) {
    if (relationship.compatibilityUnsupported || !relationship.sourceKeys.length
      || relationship.sourceKeys.length !== relationship.targetKeys.length) continue
    const key = (properties: Record<string, unknown>, columns: string[]) => {
      const values = columns.map(column => properties[column])
      return values.some(value => value === undefined || value === null) ? undefined : JSON.stringify(values)
    }
    const targets = new Map<string, OntologyGraph['nodes']>()
    for (const node of nodes.filter(node => node.entityTypeId === relationship.targetEntityTypeId)) {
      const value = key(node.properties, relationship.targetKeys)
      if (value !== undefined) targets.set(value, [...(targets.get(value) ?? []), node])
    }
    for (const source of nodes.filter(node => node.entityTypeId === relationship.sourceEntityTypeId)) {
      const value = key(source.properties, relationship.sourceKeys)
      for (const target of value === undefined ? [] : targets.get(value) ?? []) {
        edges.push({
          oid: `${relationship.id}:${source.oid}:${target.oid}`, labels: [relationship.name],
          relationshipTypeId: relationship.id, sourceOid: source.oid, targetOid: target.oid, properties: {},
        })
      }
    }
  }
  return { nodes, edges, error: undefined }
}

/** Governed relationships determine presentation context in both loading paths. */
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
  if (native?.ontologyId && native.ontologyId !== contract.id) return unavailable('The native backing graph belongs to a different Ontology than the current contract. Refresh ontology discovery and retry.')
  if (native && !native.graphModelId) return unavailable('The native graph result has no verified backing graph identity. Refresh ontology discovery and retry.')
  if (native && !native.nodes.length) return unavailable('The selected Ontology backing graph contains no entities. Check Manage graph materialization and the verified workspace, Ontology, and graph mapping, then retry.')
  const topology = native ?? boundOntologyEntities(input, contract)
  if ('error' in topology && topology.error) return unavailable(topology.error)
  const nodes: KnowledgeNode[] = []
  const edges: KnowledgeEdge[] = []
  const signalBindings: Array<{ source: KnowledgeNode; target: KnowledgeNode }> = []
  const readings = new Map(input.telemetry.map(item => [item.opcuaNodeId, item]))
  const byOid = new Map<string, KnowledgeNode>()
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
    if (nodes.some(node => node.id === id)) return unavailable(`Native entities have an ambiguous ${type} identity ${entityId}; cannot safely join operational context.`)
    const opcuaNodeId = (type === 'signal' || type === 'instrument') ? text(properties.opcua_node_id) : undefined
    const reading = opcuaNodeId ? readings.get(opcuaNodeId) : undefined
    let label = text(properties.tag ?? properties.facility_name ?? properties.system_name ?? properties.equipment_name
      ?? Object.entries(properties).find(([key]) => key.endsWith('_name') || key === 'name')?.[1]) ?? entityId ?? raw.oid
    let subtitle = (entity?.localName ?? raw.labels[0] ?? 'Ontology entity').replaceAll('_', ' ')
    let presentation = properties
    let provenance = native
      ? `Fabric Ontology · ${native.graphModelName} · ${type === 'ontology' ? `${entity?.localName ?? raw.labels[0] ?? 'Ontology entity'} ` : ''}materialized graph node`
      : `Fabric Ontology compatibility mode · Lakehouse entity binding · ${entity?.sourceTable}`
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
      provenance = `Fabric Ontology · ${native?.graphModelName ?? contract.displayName} · signal_master node with Eventhouse time-series binding`
    }
    const node: KnowledgeNode = {
      id, entityId: entityId ?? raw.oid, type, label, subtitle,
      status: type === 'equipment' ? 'nodata' : type === 'instrument' || type === 'signal' ? twinSignalStatus({ id, label, nodeId: opcuaNodeId ?? '', value: reading?.value, quality: reading?.quality }) : 'ok',
      nativeOid: native ? raw.oid : undefined, ontologyEntityTypeId: entity?.id,
      facilityId: type === 'facility' ? entityId : undefined,
      equipmentId: type === 'equipment' ? entityId : undefined,
      properties: presentGraphProperties(presentation), reading, provenance,
    }
    nodes.push(node)
    byOid.set(raw.oid, node)
  }

  for (const raw of topology.edges) {
    const source = byOid.get(raw.sourceOid)
    const target = byOid.get(raw.targetOid)
    if (!source || !target) return unavailable(`Native relationship ${raw.oid} references an entity missing from the graph result. Retry the complete graph query.`)
    if (edges.some(item => item.id === `${native ? 'native' : 'binding'}-edge:${raw.oid}`)) return unavailable(`Graph has duplicate relationship OID ${raw.oid}.`)
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
    const type = source.type === 'signal' || target.type === 'signal' ? 'has-signal' : source.type === 'instrument' || target.type === 'instrument' ? 'has-instrument' : 'contains'
    edges.push({
      id: `${native ? 'native' : 'binding'}-edge:${raw.oid}`, nativeOid: native ? raw.oid : undefined,
      source: native ? source.id : target.id, target: native ? target.id : source.id, type,
      label: (relationship?.label ?? (name || 'RELATED TO')).replaceAll('_', ' ').toUpperCase(),
      ontologyRelationshipId: relationship?.id, ontologyRelationshipName: relationship?.name,
      provenance: native
        ? `Native relationship ${raw.oid} · labels: ${raw.labels.join(', ')}${relationship ? ` · Ontology ${relationship.name} (${relationship.id})` : ' · relationship type not identified in contract'}`
        : `Fabric Ontology ${relationship?.name} · Lakehouse relationship binding`,
    })
  }

  const sourceCounts = new Map<string, number>()
  const targetCounts = new Map<string, number>()
  for (const { source, target } of signalBindings) {
    sourceCounts.set(source.id, (sourceCounts.get(source.id) ?? 0) + 1)
    targetCounts.set(target.id, (targetCounts.get(target.id) ?? 0) + 1)
  }
  const collapsed = new Map<string, string>()
  for (const { source, target } of signalBindings) {
    if (sourceCounts.get(source.id) !== 1 || targetCounts.get(target.id) !== 1) continue
    collapsed.set(source.id, target.id)
    target.properties['Signal entity'] = source.entityId
    target.provenance = `Fabric Ontology · ${native?.graphModelName ?? contract.displayName} · combined one-to-one instruments + signal_master node with Eventhouse time-series binding`
  }
  for (let index = nodes.length - 1; index >= 0; index--) if (collapsed.has(nodes[index].id)) nodes.splice(index, 1)
  for (let index = edges.length - 1; index >= 0; index--) {
    const relationship = edges[index]
    const remapped = collapsed.has(relationship.source) || collapsed.has(relationship.target)
    relationship.source = collapsed.get(relationship.source) ?? relationship.source
    relationship.target = collapsed.get(relationship.target) ?? relationship.target
    if (remapped && relationship.source === relationship.target) edges.splice(index, 1)
  }

  for (const node of nodes) {
    if (node.type === 'system') {
      const count = relatedNodes(node, nodes, edges, 'equipment', []).length
      node.subtitle = `${count} connected assets`
      node.properties['Equipment count'] = count
    }
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
  const criticalOpenNodeIds = new Set(orders.filter(order =>
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

  for (const model of input.models.filter(item => equipment.has(item.equipmentId))) {
    nodes.push({ id: nodeId('model', model.id), entityId: model.id, type: 'model', label: model.modelName, subtitle: `${model.format}${model.version ? ` · ${model.version}` : ''}`, status: 'ok', equipmentId: model.equipmentId, properties: { Format: model.format, Version: model.version, URL: model.modelUrl, 'File size MB': model.fileSizeMb }, provenance: 'Rayfin operational database · Asset3DModel' })
    edges.push(edge(equipment.get(model.equipmentId)!.id, nodeId('model', model.id), 'has-model', 'HAS MODEL'))
  }
  for (const order of orders) {
    const closed = ['completed', 'cancelled'].includes(order.status.toLowerCase())
    nodes.push({ id: nodeId('work-order', order.id), entityId: order.workOrderNumber, type: 'work-order', label: order.workOrderNumber, subtitle: order.title, status: closed ? 'ok' : order.priority.toLowerCase() === 'critical' ? 'crit' : 'warn', equipmentId: order.equipmentId, properties: { Title: order.title, Priority: order.priority, Status: order.status, Created: String(order.createdAt), Due: order.dueAt ? String(order.dueAt) : undefined }, provenance: 'Rayfin operational database · WorkOrder' })
    edges.push(edge(nodeId('work-order', order.id), equipment.get(order.equipmentId)!.id, 'affects', 'AFFECTS'))
  }
  for (const inspection of input.inspections.filter(item => equipment.has(item.equipmentId))) {
    nodes.push({ id: nodeId('inspection', inspection.id), entityId: inspection.id, type: 'inspection', label: inspection.inspectionType, subtitle: inspection.result, status: /fail|issue|attention/i.test(inspection.result) ? 'warn' : 'ok', equipmentId: inspection.equipmentId, properties: { Result: inspection.result, Findings: inspection.findings, Inspected: String(inspection.inspectedAt), 'Next due': inspection.nextDueAt ? String(inspection.nextDueAt) : undefined }, provenance: 'Rayfin operational database · Inspection' })
    edges.push(edge(nodeId('inspection', inspection.id), equipment.get(inspection.equipmentId)!.id, 'documents', 'DOCUMENTS'))
  }
  for (const notification of input.notifications.filter(item => equipment.has(item.equipmentId))) {
    nodes.push({ id: nodeId('notification', notification.id), entityId: notification.id, type: 'notification', label: notification.summary, subtitle: `${notification.severity} · ${notification.status}`, status: /critical|high/i.test(notification.severity) ? 'crit' : 'warn', equipmentId: notification.equipmentId, properties: { Severity: notification.severity, Status: notification.status, Reported: String(notification.reportedAt), 'OPC UA node': notification.opcuaNodeId }, provenance: 'Rayfin operational database · MaintenanceNotification' })
    edges.push(edge(nodeId('notification', notification.id), equipment.get(notification.equipmentId)!.id, 'reports', 'REPORTS'))
  }
  for (const node of nodes) {
    if (!node.nativeOid && node.equipmentId) node.facilityId = equipment.get(node.equipmentId)?.facilityId
  }
  if (!native) for (const relationship of edges) {
    relationship.provenance = relationship.provenance?.replace('matched native equipment entity', 'matched Ontology-bound equipment entity')
  }
  return { nodes, edges }
}
