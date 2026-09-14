import type { Equipment, Facility, Instrument, TelemetryReading } from '../services/fabric'
import type { Asset3DModelRecord, InspectionRecord, MaintenanceNotificationRecord, WorkOrderRecord } from '../services/rayfin'
import { twinStatus, type TwinStatus } from '../twin'

export type KnowledgeNodeType = 'facility' | 'system' | 'equipment' | 'instrument' | 'model' | 'work-order' | 'inspection' | 'notification'
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
export type KnowledgeEdgeType = 'contains' | 'has-instrument' | 'has-model' | 'affects' | 'documents' | 'reports'
export type KnowledgeEdge = { id: string; source: string; target: string; type: KnowledgeEdgeType; label: string }
export type KnowledgeGraph = { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] }

export type KnowledgeGraphInput = {
  facilities: Facility[]
  equipment: Equipment[]
  instruments: Instrument[]
  telemetry: TelemetryReading[]
  workOrders: WorkOrderRecord[]
  inspections: InspectionRecord[]
  notifications: MaintenanceNotificationRecord[]
  models: Asset3DModelRecord[]
}

const nodeId = (type: KnowledgeNodeType, id: string) => `${type}:${id}`
const edge = (source: string, target: string, type: KnowledgeEdgeType, label: string): KnowledgeEdge => ({
  id: `${source}|${type}|${target}`,
  source,
  target,
  type,
  label,
})

export function buildKnowledgeGraph(input: KnowledgeGraphInput): KnowledgeGraph {
  const nodes: KnowledgeNode[] = []
  const edges: KnowledgeEdge[] = []
  const readings = new Map(input.telemetry.map(item => [item.opcuaNodeId, item]))
  const openNodeIds = new Set(input.workOrders.filter(item => !['completed', 'cancelled'].includes(item.status.toLowerCase())).map(item => item.opcuaNodeId).filter(Boolean))

  for (const facility of input.facilities) {
    nodes.push({
      id: nodeId('facility', facility.facility_id), entityId: facility.facility_id, type: 'facility',
      label: facility.facility_name, subtitle: facility.type ?? 'Facility', status: 'ok', facilityId: facility.facility_id,
      properties: { Type: facility.type, Country: facility.country, Commissioned: facility.commissioned_date, Latitude: facility.lat, Longitude: facility.lon },
      provenance: 'Fabric Ontology · Lakehouse entity binding · silver_facility',
    })
  }

  const systems = new Map<string, { facilityId: string; equipmentCount: number }>()
  for (const asset of input.equipment) {
    const systemKey = `${asset.facility_id}:${asset.system_id}`
    const system = systems.get(systemKey)
    systems.set(systemKey, { facilityId: asset.facility_id, equipmentCount: (system?.equipmentCount ?? 0) + 1 })
  }
  for (const [systemKey, system] of systems) {
    const systemId = systemKey.slice(system.facilityId.length + 1)
    nodes.push({
      id: nodeId('system', systemKey), entityId: systemId, type: 'system', label: systemId, subtitle: `${system.equipmentCount} connected assets`,
      status: 'ok', facilityId: system.facilityId, properties: { 'System ID': systemId, 'Equipment count': system.equipmentCount, Inferred: true },
      provenance: 'Fabric Ontology · inferred from equipment.system_id relationship key',
    })
    edges.push(edge(nodeId('facility', system.facilityId), nodeId('system', systemKey), 'contains', 'CONTAINS'))
  }

  for (const asset of input.equipment) {
    const id = nodeId('equipment', asset.equipment_id)
    const assetInstruments = input.instruments.filter(item => item.equipment_id === asset.equipment_id)
    const statuses = assetInstruments.map(instrument => {
      const reading = readings.get(instrument.opcua_node_id)
      return twinStatus({ id: instrument.instrument_id, label: instrument.tag ?? instrument.instrument_id, nodeId: instrument.opcua_node_id, value: reading?.value, quality: reading?.quality, hasOpenIssue: openNodeIds.has(instrument.opcua_node_id) })
    })
    const status: TwinStatus = statuses.includes('crit') ? 'crit' : statuses.includes('warn') ? 'warn' : statuses.includes('ok') ? 'ok' : 'nodata'
    nodes.push({
      id, entityId: asset.equipment_id, type: 'equipment', label: asset.tag ?? asset.equipment_id,
      subtitle: asset.equipment_type_name ?? asset.equipment_type_code ?? 'Equipment', status, facilityId: asset.facility_id, equipmentId: asset.equipment_id,
      properties: { 'Equipment ID': asset.equipment_id, Type: asset.equipment_type_name, Manufacturer: asset.manufacturer, Model: asset.model, Criticality: asset.criticality, Status: asset.status, Installed: asset.install_date, Active: asset.is_active },
      provenance: 'Fabric Ontology · Lakehouse entity binding · silver_equipment',
    })
    edges.push(edge(nodeId('system', `${asset.facility_id}:${asset.system_id}`), id, 'contains', 'CONTAINS'))
  }

  for (const instrument of input.instruments) {
    const reading = readings.get(instrument.opcua_node_id)
    const id = nodeId('instrument', instrument.instrument_id)
    nodes.push({
      id, entityId: instrument.instrument_id, type: 'instrument', label: instrument.tag ?? instrument.instrument_id,
      subtitle: reading ? `${reading.value.toLocaleString()} ${instrument.unit ?? ''}`.trim() : instrument.instrument_type ?? 'Instrument',
      status: twinStatus({ id: instrument.instrument_id, label: instrument.tag ?? instrument.instrument_id, nodeId: instrument.opcua_node_id, value: reading?.value, quality: reading?.quality, hasOpenIssue: openNodeIds.has(instrument.opcua_node_id) }),
      facilityId: instrument.facility_id, equipmentId: instrument.equipment_id, reading,
      properties: { 'Instrument ID': instrument.instrument_id, Type: instrument.instrument_type, 'OPC UA node': instrument.opcua_node_id, Unit: instrument.unit, Active: instrument.is_active, 'Latest value': reading?.value, Quality: reading?.quality, 'Event time': reading?.eventTime },
      provenance: 'Fabric Ontology · Lakehouse entity + Eventhouse time-series binding via opcua_node_id',
    })
    edges.push(edge(nodeId('equipment', instrument.equipment_id), id, 'has-instrument', 'HAS INSTRUMENT'))
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