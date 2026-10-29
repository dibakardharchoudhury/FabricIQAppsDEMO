import type { KnowledgeEdge, KnowledgeNode } from '../../knowledgeGraphModel'
import { KNOWLEDGE_TYPE_COLOR } from '../../knowledgeGraphVisuals'
import type { OntologyEntityType } from '../../../services/ontologyContract'

export type OntologyPresentationCategory = KnowledgeNode['type'] | 'weather' | 'document' | 'organization' | 'equipment-type' | 'part'

const categories: Record<OntologyPresentationCategory, { label: string; color: string; size: number }> = {
  facility: { label: 'Facilities', color: KNOWLEDGE_TYPE_COLOR.facility, size: 78 },
  system: { label: 'Systems', color: KNOWLEDGE_TYPE_COLOR.system, size: 70 },
  equipment: { label: 'Equipment', color: KNOWLEDGE_TYPE_COLOR.equipment, size: 68 },
  instrument: { label: 'Instruments', color: KNOWLEDGE_TYPE_COLOR.instrument, size: 60 },
  signal: { label: 'Signals', color: KNOWLEDGE_TYPE_COLOR.signal, size: 58 },
  ontology: { label: 'Other entity types', color: KNOWLEDGE_TYPE_COLOR.ontology, size: 62 },
  model: { label: '3D models', color: KNOWLEDGE_TYPE_COLOR.model, size: 60 },
  'work-order': { label: 'Work orders', color: KNOWLEDGE_TYPE_COLOR['work-order'], size: 64 },
  inspection: { label: 'Inspections', color: KNOWLEDGE_TYPE_COLOR.inspection, size: 60 },
  notification: { label: 'Notifications', color: KNOWLEDGE_TYPE_COLOR.notification, size: 60 },
  weather: { label: 'Weather', color: '#818cf8', size: 64 },
  document: { label: 'Documents', color: '#a3a3e6', size: 60 },
  organization: { label: 'Organizations', color: '#e879f9', size: 66 },
  'equipment-type': { label: 'Equipment types', color: '#d8b4fe', size: 66 },
  part: { label: 'Spare parts', color: '#fda4af', size: 60 },
}

const aliases: Partial<Record<string, OntologyPresentationCategory>> = {
  facility: 'facility', facilities: 'facility', station: 'facility', stations: 'facility',
  hydrostation: 'facility', hydrostations: 'facility', powerplant: 'facility', powerplants: 'facility',
  system: 'system', systems: 'system',
  equipment: 'equipment', asset: 'equipment', assets: 'equipment', turbine: 'equipment', turbines: 'equipment',
  equipmenttype: 'equipment-type', equipmenttypes: 'equipment-type',
  sparepart: 'part', spareparts: 'part',
  instrument: 'instrument', instruments: 'instrument', sensor: 'instrument', sensors: 'instrument',
  signal: 'signal', signals: 'signal', signalmaster: 'signal', telemetry: 'signal', timeseries: 'signal',
  asset3dmodel: 'model', asset3dmodels: 'model',
  workorder: 'work-order', workorders: 'work-order',
  inspection: 'inspection', inspections: 'inspection',
  maintenancenotification: 'notification', maintenancenotifications: 'notification', notification: 'notification', notifications: 'notification',
  weather: 'weather', weatherstation: 'weather', weatherstations: 'weather',
  weatherlocation: 'weather', weatherlocations: 'weather', weatherarea: 'weather', weatherareas: 'weather',
  weatherobservation: 'weather', weatherobservations: 'weather', weatherforecast: 'weather', weatherforecasts: 'weather',
  weatherdailyforecast: 'weather', weatherhourlyforecast: 'weather',
  weathersource: 'weather', weatherlatestobservation: 'weather', weatherlatestforecast: 'weather',
  forecast: 'weather', forecasts: 'weather',
  engineeringdocument: 'document', engineeringdocuments: 'document', document: 'document', documents: 'document',
  organization: 'organization', organizations: 'organization', operator: 'organization', operators: 'organization',
}

export function ontologyNodePresentation(node: KnowledgeNode) {
  // Exact business-name aliases affect appearance only, never identity or connectivity.
  const key = String(node.properties['Entity type'] ?? node.label).split('.').at(-1)!.replace(/[^a-z0-9]/gi, '').toLowerCase()
  const category = node.nativeOid && node.type !== 'ontology' ? node.type : aliases[key] ?? node.type
  return { category, ...categories[category] }
}

export function ontologyNeighborhoodIds(edges: KnowledgeEdge[], selectedId: string): Set<string> {
  const ids = new Set([selectedId])
  for (const edge of edges) {
    if (edge.source === selectedId || edge.target === selectedId) {
      ids.add(edge.source)
      ids.add(edge.target)
    }
  }
  return ids
}

export function ontologyPropertyPresentation(entity: OntologyEntityType) {
  return Object.entries(entity.properties).map(([name, value]) => {
    const metadata = entity.propertyMetadata?.[name]
    return {
      name,
      dataType: metadata?.dataType ?? (/^(string|int64|double|dateTime|boolean|decimal|Any|complex|TimeSeries<.+>)$/i.test(value) ? value : 'Not declared'),
      lineageId: metadata?.id,
    }
  })
}
