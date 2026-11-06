import type { DefinitionPart, OntologyContract, OntologyEntityType } from '../services/ontologyContract'
import { fingerprint, type Contract, type Issue } from './contract'

export async function definitionFingerprint(parts: DefinitionPart[]): Promise<string> {
  return fingerprint(parts.map(part => ({ path: part.path, payload: part.payload, payloadType: part.payloadType }))
    .sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
}

function physicalColumn(type: OntologyEntityType | undefined, property: string | undefined): string | undefined {
  const value = property && type?.propertyMetadata?.[property]?.backingConfiguration?.valueColumn
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const reference = value as Record<string, unknown>
  return reference.table === type.sourceTable && typeof reference.column === 'string' ? reference.column : undefined
}

export function ontologyIssues(contract: Contract, live: OntologyContract): Issue[] {
  const issues: Issue[] = []
  for (const entity of contract.entities) {
    const type = live.entityTypes.find(e => e.name === entity.ontologyEntity)
    if (!type) { issues.push({ code: 'ontology_entity', message: `${entity.id}: ${entity.ontologyEntity} is not a declared ontology entity.` }); continue }
    for (const p of entity.properties) {
      if (type.propertyMetadata?.[p.ontologyProperty]?.dataType !== p.type) issues.push({
        code: 'ontology_property', message: `${entity.id}.${p.name}: ontology property ${p.ontologyProperty} is missing or has a different type.`,
      })
    }
    const key = entity.properties.find(p => p.name === entity.key)
    const keyId = key && type.propertyMetadata?.[key.ontologyProperty]?.id
    if (!keyId || type.entityIdParts.length !== 1 || type.entityIdParts[0] !== keyId) {
      issues.push({ code: 'ontology_key', message: `${entity.id}: the canonical key must match the ontology's declared single-property key.` })
    }
  }
  for (const r of contract.relationships) {
    const from = contract.entities.find(e => e.id === r.from), to = contract.entities.find(e => e.id === r.to)
    const fromType = live.entityTypes.find(e => e.name === from?.ontologyEntity), toType = live.entityTypes.find(e => e.name === to?.ontologyEntity)
    const relation = live.relationshipTypes.find(edge => edge.name === r.ontologyRelationship)
    const fromColumn = physicalColumn(fromType, from?.properties.find(p => p.name === r.column)?.ontologyProperty)
    const toColumn = physicalColumn(toType, to?.properties.find(p => p.name === to.key)?.ontologyProperty)
    if (!fromColumn || !toColumn || !relation || relation.compatibilityUnsupported || relation.sourceEntityTypeId !== fromType?.id
      || relation.targetEntityTypeId !== toType?.id || relation.sourceCardinality !== 'many' || relation.targetCardinality !== 'one'
      || JSON.stringify(relation.sourceKeys) !== JSON.stringify([fromColumn])
      || JSON.stringify(relation.targetKeys) !== JSON.stringify([toColumn])) issues.push({
      code: 'ontology_relationship', message: `${r.name}: live ontology must explicitly declare this many-to-one relationship and both physical property bindings.`,
    })
  }
  return issues
}
