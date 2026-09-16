export type OntologyEntityType = {
  id: string
  name: string
  entityIdParts: string[]
  properties: Record<string, string>
  sourceTable?: string
}

export type OntologyRelationshipType = {
  id: string
  name: string
  sourceEntityTypeId: string
  targetEntityTypeId: string
  sourceEntityName: string
  targetEntityName: string
  sourceKeys: string[]
  targetKeys: string[]
}

export type OntologyContract = {
  id: string
  displayName: string
  entityTypes: OntologyEntityType[]
  relationshipTypes: OntologyRelationshipType[]
}

export type DefinitionPart = { path: string; payload?: string; payloadType?: string }
export type OntologyDefinition = { definition?: { parts?: DefinitionPart[] } }

function decodeDefinitionPart(part: DefinitionPart): Record<string, unknown> | undefined {
  if (!part.payload || part.payloadType !== 'InlineBase64') return undefined
  try {
    const bytes = Uint8Array.from(atob(part.payload), char => char.charCodeAt(0))
    return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>
  } catch {
    return undefined
  }
}

function bindingColumns(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(item => {
    if (!item || typeof item !== 'object') return []
    const sourceColumnName = (item as { sourceColumnName?: unknown }).sourceColumnName
    return typeof sourceColumnName === 'string' ? [sourceColumnName] : []
  })
}

export function parseOntologyContract(id: string, displayName: string, definition: OntologyDefinition): OntologyContract {
  const parts = definition.definition?.parts ?? []
  const entityTypes: OntologyEntityType[] = []
  const rawRelationships: Array<Omit<OntologyRelationshipType, 'sourceEntityName' | 'targetEntityName' | 'sourceKeys' | 'targetKeys'>> = []
  const contextualizations = new Map<string, { sourceKeys: string[]; targetKeys: string[] }>()

  for (const part of parts) {
    const payload = decodeDefinitionPart(part)
    if (!payload) continue
    const entityMatch = part.path.match(/^EntityTypes\/([^/]+)\/definition\.json$/)
    if (entityMatch) {
      const properties = Array.isArray(payload.properties) ? payload.properties : []
      const sourcePart = parts.find(candidate => candidate.path.startsWith(`EntityTypes/${entityMatch[1]}/DataBindings/`))
      const source = sourcePart ? decodeDefinitionPart(sourcePart)?.dataBindingConfiguration as { sourceTableProperties?: { tableName?: string } } | undefined : undefined
      entityTypes.push({
        id: String(payload.id ?? entityMatch[1]),
        name: String(payload.name ?? ''),
        entityIdParts: Array.isArray(payload.entityIdParts) ? payload.entityIdParts.map(String) : [],
        properties: Object.fromEntries(properties.flatMap(property => property && typeof property === 'object'
          ? [[String((property as { name?: unknown }).name ?? ''), String((property as { id?: unknown }).id ?? '')]]
          : [])),
        sourceTable: source?.sourceTableProperties?.tableName,
      })
      continue
    }
    const relationshipMatch = part.path.match(/^RelationshipTypes\/([^/]+)\/definition\.json$/)
    if (relationshipMatch) {
      const source = payload.source as { entityTypeId?: unknown } | undefined
      const target = payload.target as { entityTypeId?: unknown } | undefined
      rawRelationships.push({ id: String(payload.id ?? relationshipMatch[1]), name: String(payload.name ?? ''), sourceEntityTypeId: String(source?.entityTypeId ?? ''), targetEntityTypeId: String(target?.entityTypeId ?? '') })
      continue
    }
    const contextualizationMatch = part.path.match(/^RelationshipTypes\/([^/]+)\/Contextualizations\//)
    if (contextualizationMatch) contextualizations.set(contextualizationMatch[1], {
      sourceKeys: bindingColumns(payload.sourceKeyRefBindings),
      targetKeys: bindingColumns(payload.targetKeyRefBindings),
    })
  }

  const entityNames = new Map(entityTypes.map(entity => [entity.id, entity.name]))
  return {
    id,
    displayName,
    entityTypes,
    relationshipTypes: rawRelationships.map(relationship => ({
      ...relationship,
      sourceEntityName: entityNames.get(relationship.sourceEntityTypeId) ?? '',
      targetEntityName: entityNames.get(relationship.targetEntityTypeId) ?? '',
      sourceKeys: contextualizations.get(relationship.id)?.sourceKeys ?? [],
      targetKeys: contextualizations.get(relationship.id)?.targetKeys ?? [],
    })),
  }
}