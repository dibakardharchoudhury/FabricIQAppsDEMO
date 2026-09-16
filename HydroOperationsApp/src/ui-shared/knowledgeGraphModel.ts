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
  properties: Record<string, string | number | boolean | undefined>
  provenance: string
  reading?: TelemetryReading
}
export type KnowledgeEdgeType = 'contains' | 'has-instrument' | 'has-signal' | 'has-model' | 'affects' | 'documents' | 'reports'
export type KnowledgeEdge = { id: string; source: string; target: string; type: KnowledgeEdgeType; label: string; ontologyRelationshipId?: string; ontologyRelationshipName?: string }
export type KnowledgeGraph = { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] }

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
const edge = (source: string, target: string, type: KnowledgeEdgeType, label: string, ontology?: { id: string; name: string }): KnowledgeEdge => ({
  id: `${source}|${type}|${target}`,
  source,
  target,
  type,
  label,
  ontologyRelationshipId: ontology?.id,
  ontologyRelationshipName: ontology?.name,
})

const graphEntity = (label: string, properties: Record<string, unknown>): Facility | System | Equipment | Instrument | undefined => {
  if (label === 'facilities') return properties as Facility
  if (label === 'systems') return properties as System
  if (label === 'equipment') return properties as Equipment
  if (label === 'instruments') return properties as Instrument
  return undefined
}

const graphNodeId = (label: string, properties: Record<string, unknown>, oid?: string): string | undefined => {
  if (label === 'facilities' && properties.facility_id) return nodeId('facility', String(properties.facility_id))
  if (label === 'systems' && properties.system_id) return nodeId('system', String(properties.system_id))
  if (label === 'equipment' && properties.equipment_id) return nodeId('equipment', String(properties.equipment_id))
  if (label === 'instruments' && properties.instrument_id) return nodeId('instrument', String(properties.instrument_id))
  if (label === 'signal_master' && properties.opcua_node_id) return nodeId('signal', String(properties.opcua_node_id))
  return oid ? nodeId('ontology', oid) : undefined
}

const displayProperty = (properties: Record<string, unknown>, suffix: string) => Object.entries(properties).find(([key]) => key.endsWith(suffix))?.[1]
const presentGraphProperties = (properties: Record<string, unknown>): KnowledgeNode['properties'] => Object.fromEntries(Object.entries(properties).map(([key, value]) => [key, typeof value === 'object' && value !== null ? JSON.stringify(value) : value])) as KnowledgeNode['properties']

export function buildKnowledgeGraph(input: KnowledgeGraphInput): KnowledgeGraph {
  const nodes: KnowledgeNode[] = []
  const edges: KnowledgeEdge[] = []
  const readings = new Map(input.telemetry.map(item => [item.opcuaNodeId, item]))
  const openNodeIds = new Set(input.workOrders.filter(item => !['completed', 'cancelled'].includes(item.status.toLowerCase())).map(item => item.opcuaNodeId).filter(Boolean))
  const ontologyRelationship = (source: string, target: string) => input.ontology?.relationshipTypes.find(item => item.sourceEntityName === source && item.targetEntityName === target)
  const relationshipLabel = (relationship: { name: string } | undefined, fallback: string) => relationship?.name.replaceAll('_', ' ').toUpperCase() ?? fallback
  const graphNodes = input.ontologyGraph?.nodes ?? []
  const graphEntities = (label: string) => graphNodes.filter(item => item.labels.includes(label)).map(item => graphEntity(label, item.properties)).filter(Boolean)
  const facilities = input.ontologyGraph ? graphEntities('facilities') as Facility[] : input.facilities
  const systems = input.ontologyGraph ? graphEntities('systems') as System[] : input.systems
  const equipment = input.ontologyGraph ? graphEntities('equipment') as Equipment[] : input.equipment
  const instruments = input.ontologyGraph ? graphEntities('instruments') as Instrument[] : input.instruments

  for (const facility of facilities) {
    nodes.push({
      id: nodeId('facility', facility.facility_id), entityId: facility.facility_id, type: 'facility',
      label: facility.facility_name, subtitle: facility.type ?? 'Facility', status: 'ok', facilityId: facility.facility_id,
      properties: { Type: facility.type, Country: facility.country, Commissioned: facility.commissioned_date, Latitude: facility.lat, Longitude: facility.lon },
      provenance: input.ontologyGraph ? `Fabric Ontology · ${input.ontologyGraph.graphModelName} · materialized graph node` : 'Fabric Ontology compatibility mode · Lakehouse entity binding · silver_facilities',
    })
  }

  const systemsInOntology = input.ontology?.entityTypes.some(entity => entity.name === 'systems') ?? false
  for (const system of systems) {
    const equipmentCount = equipment.filter(asset => asset.system_id === system.system_id).length
    nodes.push({
      id: nodeId('system', system.system_id), entityId: system.system_id, type: 'system', label: system.system_name ?? system.system_id, subtitle: `${equipmentCount} connected assets`,
      status: 'ok', facilityId: system.facility_id, properties: { 'System ID': system.system_id, 'OAG RDS code': system.oag_rds_system_code, 'Equipment count': equipmentCount },
      provenance: input.ontologyGraph ? `Fabric Ontology · ${input.ontologyGraph.graphModelName} · materialized graph node` : `Fabric Ontology${systemsInOntology ? ` ${input.ontology?.displayName}` : ''} compatibility mode · Lakehouse entity binding · silver_systems`,
    })
    const relationship = ontologyRelationship('systems', 'facilities')
    if (!input.ontologyGraph && (!input.ontology || relationship)) edges.push(edge(nodeId('facility', system.facility_id), nodeId('system', system.system_id), 'contains', relationshipLabel(relationship, 'CONTAINS'), relationship))
  }

  for (const asset of equipment) {
    const id = nodeId('equipment', asset.equipment_id)
    const assetInstruments = instruments.filter(item => item.equipment_id === asset.equipment_id)
    const statuses = assetInstruments.map(instrument => {
      const reading = readings.get(instrument.opcua_node_id)
      return twinStatus({ id: instrument.instrument_id, label: instrument.tag ?? instrument.instrument_id, nodeId: instrument.opcua_node_id, value: reading?.value, quality: reading?.quality, hasOpenIssue: openNodeIds.has(instrument.opcua_node_id) })
    })
    const status: TwinStatus = statuses.includes('crit') ? 'crit' : statuses.includes('warn') ? 'warn' : statuses.includes('ok') ? 'ok' : 'nodata'
    nodes.push({
      id, entityId: asset.equipment_id, type: 'equipment', label: asset.tag ?? asset.equipment_id,
      subtitle: asset.equipment_type_name ?? asset.equipment_type_code ?? 'Equipment', status, facilityId: asset.facility_id, equipmentId: asset.equipment_id,
      properties: { 'Equipment ID': asset.equipment_id, Type: asset.equipment_type_name, Manufacturer: asset.manufacturer, Model: asset.model, Criticality: asset.criticality, Status: asset.status, Installed: asset.install_date, Active: asset.is_active },
      provenance: input.ontologyGraph ? `Fabric Ontology · ${input.ontologyGraph.graphModelName} · materialized graph node` : 'Fabric Ontology compatibility mode · Lakehouse entity binding · silver_equipment',
    })
    const relationship = ontologyRelationship('equipment', 'systems')
    if (!input.ontologyGraph && (!input.ontology || relationship)) edges.push(edge(nodeId('system', asset.system_id), id, 'contains', relationshipLabel(relationship, 'CONTAINS'), relationship))
  }

  for (const instrument of instruments) {
    const reading = readings.get(instrument.opcua_node_id)
    const id = nodeId('instrument', instrument.instrument_id)
    nodes.push({
      id, entityId: instrument.instrument_id, type: 'instrument', label: instrument.tag ?? instrument.instrument_id,
      subtitle: reading ? `${reading.value.toLocaleString()} ${instrument.unit ?? ''}`.trim() : instrument.instrument_type ?? 'Instrument',
      status: twinStatus({ id: instrument.instrument_id, label: instrument.tag ?? instrument.instrument_id, nodeId: instrument.opcua_node_id, value: reading?.value, quality: reading?.quality, hasOpenIssue: openNodeIds.has(instrument.opcua_node_id) }),
      facilityId: instrument.facility_id, equipmentId: instrument.equipment_id, reading,
      properties: { 'Instrument ID': instrument.instrument_id, Type: instrument.instrument_type, 'OPC UA node': instrument.opcua_node_id, Unit: instrument.unit, Active: instrument.is_active, 'Latest value': reading?.value, Quality: reading?.quality, 'Event time': reading?.eventTime },
      provenance: input.ontologyGraph ? `Fabric Ontology · ${input.ontologyGraph.graphModelName} · materialized graph node` : 'Fabric Ontology compatibility mode · Lakehouse entity + Eventhouse time-series binding via opcua_node_id',
    })
    const relationship = ontologyRelationship('instruments', 'equipment')
    if (!input.ontologyGraph && (!input.ontology || relationship)) edges.push(edge(nodeId('equipment', instrument.equipment_id), id, 'has-instrument', relationshipLabel(relationship, 'HAS INSTRUMENT'), relationship))
  }

  if (input.ontologyGraph) {
    const signalBindings = input.ontologyGraph.edges.filter(relationship => relationship.labels.includes('signals_from_instruments'))
    const sourceBindingCounts = new Map<string, number>()
    const targetBindingCounts = new Map<string, number>()
    for (const binding of signalBindings) {
      sourceBindingCounts.set(binding.sourceOid, (sourceBindingCounts.get(binding.sourceOid) ?? 0) + 1)
      targetBindingCounts.set(binding.targetOid, (targetBindingCounts.get(binding.targetOid) ?? 0) + 1)
    }
    const collapsedSignalTargets = new Map(signalBindings
      .filter(binding => sourceBindingCounts.get(binding.sourceOid) === 1 && targetBindingCounts.get(binding.targetOid) === 1)
      .map(binding => [binding.sourceOid, binding.targetOid]))
    const appIdsByOid = new Map(graphNodes.flatMap(item => {
      const label = item.labels[0]
      const id = graphNodeId(label, item.properties, item.oid)
      return id ? [[item.oid, id] as const] : []
    }))
    for (const [signalOid, instrumentOid] of collapsedSignalTargets) {
      const instrumentId = appIdsByOid.get(instrumentOid)
      if (instrumentId) appIdsByOid.set(signalOid, instrumentId)
    }
    for (const signal of graphNodes.filter(item => item.labels.includes('signal_master'))) {
      const properties = signal.properties
      const opcuaNodeId = String(properties.opcua_node_id ?? '')
      if (!opcuaNodeId) continue
      const reading = readings.get(opcuaNodeId)
      const signalId = String(properties.instrument_id ?? opcuaNodeId)
      const collapsedInstrumentId = appIdsByOid.get(signal.oid)
      if (collapsedSignalTargets.has(signal.oid) && collapsedInstrumentId) {
        const instrumentNode = nodes.find(node => node.id === collapsedInstrumentId)
        if (instrumentNode) {
          instrumentNode.properties = { ...instrumentNode.properties, 'Signal entity': opcuaNodeId }
          instrumentNode.provenance = `Fabric Ontology · ${input.ontologyGraph.graphModelName} · combined one-to-one instruments + signal_master node with Eventhouse time-series binding`
          continue
        }
      }
      const signalLabel = String(properties.tag ?? properties.signal_type ?? signalId)
      nodes.push({
        id: nodeId('signal', opcuaNodeId), entityId: opcuaNodeId, type: 'signal', label: `Signal · ${signalLabel}`,
        subtitle: reading ? `${reading.value.toLocaleString()} ${String(properties.unit ?? '')}`.trim() : String(properties.signal_type ?? 'Time-series signal'),
        status: twinStatus({ id: signalId, label: signalLabel, nodeId: opcuaNodeId, value: reading?.value, quality: reading?.quality, hasOpenIssue: openNodeIds.has(opcuaNodeId) }),
        facilityId: String(properties.facility_id ?? '') || undefined, equipmentId: String(properties.equipment_id ?? '') || undefined, reading,
        properties: { ...properties, 'Latest value': reading?.value, Quality: reading?.quality, 'Event time': reading?.eventTime } as KnowledgeNode['properties'],
        provenance: `Fabric Ontology · ${input.ontologyGraph.graphModelName} · signal_master node with Eventhouse time-series binding`,
      })
    }
    const specializedLabels = new Set(['facilities', 'systems', 'equipment', 'instruments', 'signal_master'])
    for (const graphNode of graphNodes.filter(item => !item.labels.some(label => specializedLabels.has(label)))) {
      const label = graphNode.labels[0] ?? 'Ontology entity'
      const entityId = String(displayProperty(graphNode.properties, '_id') ?? graphNode.oid)
      nodes.push({
        id: nodeId('ontology', graphNode.oid), entityId, type: 'ontology',
        label: String(displayProperty(graphNode.properties, '_name') ?? displayProperty(graphNode.properties, 'name') ?? entityId),
        subtitle: label.replaceAll('_', ' '), status: 'ok',
        facilityId: typeof graphNode.properties.facility_id === 'string' ? graphNode.properties.facility_id : undefined,
        equipmentId: typeof graphNode.properties.equipment_id === 'string' ? graphNode.properties.equipment_id : undefined,
        properties: presentGraphProperties(graphNode.properties),
        provenance: `Fabric Ontology · ${input.ontologyGraph.graphModelName} · ${label} materialized graph node`,
      })
    }
    const relationshipByName = new Map(input.ontology?.relationshipTypes.map(item => [item.name, item]) ?? [])
    for (const relationship of input.ontologyGraph.edges) {
      const source = appIdsByOid.get(relationship.sourceOid)
      const target = appIdsByOid.get(relationship.targetOid)
      if (!source || !target || source === target) continue
      const label = relationship.labels[0] ?? 'RELATED TO'
      const contractRelationship = relationshipByName.get(label)
      const type: KnowledgeEdgeType = label === 'instruments_on_equipment' ? 'has-instrument' : label === 'signals_from_instruments' ? 'has-signal' : 'contains'
      edges.push(edge(source, target, type, label.replaceAll('_', ' ').toUpperCase(), contractRelationship ?? { id: relationship.oid, name: label }))
    }
  }

  for (const model of input.models) {
    nodes.push({ id: nodeId('model', model.id), entityId: model.id, type: 'model', label: model.modelName, subtitle: `${model.format}${model.version ? ` · ${model.version}` : ''}`, status: 'ok', equipmentId: model.equipmentId, properties: { Format: model.format, Version: model.version, URL: model.modelUrl, 'File size MB': model.fileSizeMb }, provenance: 'Rayfin operational database · Asset3DModel' })
    edges.push(edge(nodeId('equipment', model.equipmentId), nodeId('model', model.id), 'has-model', 'HAS MODEL'))
  }
  for (const order of input.workOrders) {
    const closed = ['completed', 'cancelled'].includes(order.status.toLowerCase())
    nodes.push({ id: nodeId('work-order', order.id), entityId: order.workOrderNumber, type: 'work-order', label: order.workOrderNumber, subtitle: order.title, status: closed ? 'ok' : order.priority.toLowerCase() === 'critical' ? 'crit' : 'warn', equipmentId: order.equipmentId, properties: { Title: order.title, Priority: order.priority, Status: order.status, Created: String(order.createdAt), Due: order.dueAt ? String(order.dueAt) : undefined }, provenance: 'Rayfin operational database · WorkOrder' })
    edges.push(edge(nodeId('work-order', order.id), nodeId('equipment', order.equipmentId), 'affects', 'AFFECTS'))
  }
  for (const inspection of input.inspections) {
    nodes.push({ id: nodeId('inspection', inspection.id), entityId: inspection.id, type: 'inspection', label: inspection.inspectionType, subtitle: inspection.result, status: /fail|issue|attention/i.test(inspection.result) ? 'warn' : 'ok', equipmentId: inspection.equipmentId, properties: { Result: inspection.result, Findings: inspection.findings, Inspected: String(inspection.inspectedAt), 'Next due': inspection.nextDueAt ? String(inspection.nextDueAt) : undefined }, provenance: 'Rayfin operational database · Inspection' })
    edges.push(edge(nodeId('inspection', inspection.id), nodeId('equipment', inspection.equipmentId), 'documents', 'DOCUMENTS'))
  }
  for (const notification of input.notifications) {
    nodes.push({ id: nodeId('notification', notification.id), entityId: notification.id, type: 'notification', label: notification.summary, subtitle: `${notification.severity} · ${notification.status}`, status: /critical|high/i.test(notification.severity) ? 'crit' : 'warn', equipmentId: notification.equipmentId, properties: { Severity: notification.severity, Status: notification.status, Reported: String(notification.reportedAt), 'OPC UA node': notification.opcuaNodeId }, provenance: 'Rayfin operational database · MaintenanceNotification' })
    edges.push(edge(nodeId('notification', notification.id), nodeId('equipment', notification.equipmentId), 'reports', 'REPORTS'))
  }

  const ids = new Set(nodes.map(item => item.id))
  return { nodes, edges: edges.filter(item => ids.has(item.source) && ids.has(item.target)) }
}