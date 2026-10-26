import { parseTmdlContract } from './ontologyTmdl'
import { requireV2Generation, V2_REPLACEMENT_REQUIRED } from './ontologyArtifactDiscovery'

export type OntologyProperty = {
  id: string
  dataType: string
  complexDataType?: Record<string, unknown>
  backingConfiguration?: Record<string, unknown>
}

export type OntologyEntityType = {
  id: string
  name: string
  entityIdParts: string[]
  properties: Record<string, string>
  sourceTable?: string
  namespace?: string
  localName?: string
  propertyMetadata?: Record<string, OntologyProperty>
  additionalBackingTables?: Array<{ table: string; relationship: string }>
}

export type OntologyRelationshipType = {
  id: string
  name: string
  label?: string
  sourceEntityTypeId: string
  targetEntityTypeId: string
  sourceEntityName: string
  targetEntityName: string
  sourceKeys: string[]
  targetKeys: string[]
  backingRelationship?: string
  compatibilityUnsupported?: boolean
}

export type OntologyContract = {
  id: string
  displayName: string
  entityTypes: OntologyEntityType[]
  relationshipTypes: OntologyRelationshipType[]
  generation: 2
  warnings?: string[]
}

export type DefinitionPart = { path: string; payload?: string; payloadType?: string }
export type OntologyDefinition = { definition?: { parts?: DefinitionPart[] } }

export function decodeDefinitionText(part: DefinitionPart): string {
  if (typeof part.payload !== 'string' || part.payloadType !== 'InlineBase64') {
    throw new Error(`Ontology definition ${part.path}: expected an InlineBase64 payload. Refresh the definition or check Fabric item read access.`)
  }
  try {
    const bytes = Uint8Array.from(atob(part.payload), char => char.charCodeAt(0))
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new Error(`Ontology definition ${part.path}: invalid Base64 or UTF-8 payload.`)
  }
}

export function parseOntologyContract(id: string, displayName: string, definition: OntologyDefinition, generation: unknown): OntologyContract {
  requireV2Generation(generation)
  const parts = definition.definition?.parts ?? []
  if (new Set(parts.map(part => part.path)).size !== parts.length) throw new Error('Ontology definition contains duplicate part paths.')
  const hasV1 = parts.some(part => /^(EntityTypes|RelationshipTypes)\//.test(part.path))
  const hasV2 = parts.some(part => part.path.endsWith('.tmdl'))
  if (hasV1) throw new Error(V2_REPLACEMENT_REQUIRED)
  if (hasV2) return parseTmdlContract(id, displayName, parts)
  throw new Error('Ontology v2 definition has no supported TMDL parts. Publish a generation-2 TMDL definition and refresh.')
}