import type { OntologyContract, OntologyEntityType } from './ontologyContract'
import type { OntologyGraph } from './ontologyGraph'

export type KnowledgeGraphExportFormat = 'owl-turtle' | 'rdf-turtle'

const prefixes = [
  '@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .',
  '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
  '@prefix owl: <http://www.w3.org/2002/07/owl#> .',
  '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .',
  '',
]

const iriSegment = (value: string) => encodeURIComponent(value)
const ontologyIri = (ontologyId: string) => `urn:microsoft-fabric:ontology:${iriSegment(ontologyId)}`
const classIri = (ontologyId: string, entityId: string) => `${ontologyIri(ontologyId)}:entity:${iriSegment(entityId)}`
const relationshipIri = (ontologyId: string, relationshipId: string) => `${ontologyIri(ontologyId)}:relationship:${iriSegment(relationshipId)}`
const propertyIri = (ontologyId: string, entityId: string, name: string) =>
  `${classIri(ontologyId, entityId)}:property:${iriSegment(name)}`
const edgePropertyIri = (ontologyId: string, relationshipId: string, name: string) =>
  `${relationshipIri(ontologyId, relationshipId)}:property:${iriSegment(name)}`
const nodeIri = (graphModelId: string, oid: string) =>
  `urn:microsoft-fabric:graph:${iriSegment(graphModelId)}:node:${iriSegment(oid)}`
const edgeIri = (graphModelId: string, oid: string) =>
  `urn:microsoft-fabric:graph:${iriSegment(graphModelId)}:edge:${iriSegment(oid)}`
const iri = (value: string) => `<${value}>`

function textLiteral(value: string): string {
  return JSON.stringify(value)
}

function typedLiteral(value: unknown, dataType?: string): string {
  if (typeof value === 'boolean') return `"${value}"^^xsd:boolean`
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('Knowledge Graph export cannot serialize a non-finite number.')
    return Number.isInteger(value) ? `"${value}"^^xsd:integer` : `"${value}"^^xsd:double`
  }
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  const type = dataType?.toLowerCase() ?? ''
  if (type.includes('boolean') && /^(true|false)$/i.test(text)) return `"${text.toLowerCase()}"^^xsd:boolean`
  if (/(?:int|long|bigint)/.test(type) && /^-?\d+$/.test(text)) return `"${text}"^^xsd:integer`
  if (/(?:double|float|decimal)/.test(type) && Number.isFinite(Number(text))) return `"${text}"^^xsd:double`
  if (/(?:datetime|date time|zoned datetime)/.test(type)) return `${textLiteral(text)}^^xsd:dateTime`
  return textLiteral(text)
}

function xsdType(dataType: string): string {
  const type = dataType.toLowerCase()
  if (type.includes('boolean')) return 'xsd:boolean'
  if (/(?:int|long|bigint)/.test(type)) return 'xsd:integer'
  if (/(?:double|float)/.test(type)) return 'xsd:double'
  if (type.includes('decimal')) return 'xsd:decimal'
  if (/(?:datetime|date time|zoned datetime)/.test(type)) return 'xsd:dateTime'
  return 'xsd:string'
}

function typeMap(ontology: OntologyContract): Map<string, OntologyEntityType> {
  const values = new Map<string, OntologyEntityType>()
  for (const entity of ontology.entityTypes) {
    if (values.has(entity.id)) throw new Error(`Ontology export found duplicate entity type ${entity.id}.`)
    values.set(entity.id, entity)
  }
  return values
}

function validateNativeGraph(ontology: OntologyContract, graph: OntologyGraph) {
  if (ontology.generation !== 2) throw new Error('Knowledge Graph export requires a verified generation-2 Ontology.')
  if (graph.ontologyId && graph.ontologyId.toLowerCase() !== ontology.id.toLowerCase()) {
    throw new Error('Knowledge Graph export source does not match the selected Ontology.')
  }
  const entities = typeMap(ontology)
  const relationships = new Map(ontology.relationshipTypes.map(value => [value.id, value]))
  const nodeIds = new Set(graph.nodes.map(value => value.oid))
  if (nodeIds.size !== graph.nodes.length) throw new Error('Knowledge Graph export found duplicate node identities.')
  for (const node of graph.nodes) {
    if (!node.entityTypeId || !entities.has(node.entityTypeId)) {
      throw new Error(`Knowledge Graph node ${node.oid} has no verified Ontology entity type.`)
    }
  }
  for (const edge of graph.edges) {
    const relationship = edge.relationshipTypeId && relationships.get(edge.relationshipTypeId)
    if (!relationship) throw new Error(`Knowledge Graph edge ${edge.oid} has no verified Ontology relationship type.`)
    if (!nodeIds.has(edge.sourceOid) || !nodeIds.has(edge.targetOid)) {
      throw new Error(`Knowledge Graph edge ${edge.oid} has an endpoint outside the native graph.`)
    }
  }
  return { entities, relationships }
}

function statement(subject: string, predicate: string, object: string): string {
  return `${iri(subject)} ${predicate} ${object} .`
}

export function serializeNativeKnowledgeGraph(
  ontology: OntologyContract,
  graph: OntologyGraph,
  format: KnowledgeGraphExportFormat,
): string {
  const { entities, relationships } = validateNativeGraph(ontology, graph)
  const owl = format === 'owl-turtle'
  const lines = [...prefixes]
  lines.push(statement(
    ontologyIri(ontology.id),
    'a',
    owl ? 'owl:Ontology' : 'rdfs:Resource',
  ))
  lines.push(statement(ontologyIri(ontology.id), 'rdfs:label', textLiteral(ontology.displayName)))
  lines.push(statement(
    ontologyIri(ontology.id),
    iri(`${ontologyIri(ontology.id)}:graphModelId`),
    textLiteral(graph.graphModelId),
  ))
  lines.push('')

  for (const entity of [...ontology.entityTypes].sort((left, right) => left.id.localeCompare(right.id))) {
    const entityIri = classIri(ontology.id, entity.id)
    lines.push(statement(entityIri, 'a', owl ? 'owl:Class' : 'rdfs:Class'))
    lines.push(statement(entityIri, 'rdfs:label', textLiteral(entity.name)))
    for (const [name, metadata] of Object.entries(entity.propertyMetadata ?? {}).sort(([left], [right]) => left.localeCompare(right))) {
      const predicate = propertyIri(ontology.id, entity.id, name)
      lines.push(statement(predicate, 'a', owl ? 'owl:DatatypeProperty' : 'rdf:Property'))
      lines.push(statement(predicate, 'rdfs:label', textLiteral(name)))
      lines.push(statement(predicate, 'rdfs:domain', iri(entityIri)))
      if (owl) lines.push(statement(predicate, 'rdfs:range', xsdType(metadata.dataType)))
    }
    lines.push('')
  }

  for (const relationship of [...ontology.relationshipTypes].sort((left, right) => left.id.localeCompare(right.id))) {
    const predicate = relationshipIri(ontology.id, relationship.id)
    lines.push(statement(predicate, 'a', owl ? 'owl:ObjectProperty' : 'rdf:Property'))
    lines.push(statement(predicate, 'rdfs:label', textLiteral(relationship.label ?? relationship.name)))
    lines.push(statement(predicate, 'rdfs:domain', iri(classIri(ontology.id, relationship.sourceEntityTypeId))))
    lines.push(statement(predicate, 'rdfs:range', iri(classIri(ontology.id, relationship.targetEntityTypeId))))
    lines.push('')
  }

  for (const node of [...graph.nodes].sort((left, right) => left.oid.localeCompare(right.oid))) {
    const entity = entities.get(node.entityTypeId!)!
    const subject = nodeIri(graph.graphModelId, node.oid)
    const keyName = Object.entries(entity.properties)
      .find(([, propertyId]) => entity.entityIdParts.includes(propertyId))?.[0]
    lines.push(statement(subject, 'a', iri(classIri(ontology.id, entity.id))))
    if (owl) lines.push(statement(subject, 'a', 'owl:NamedIndividual'))
    lines.push(statement(subject, 'rdfs:label', textLiteral(String(
      keyName ? node.properties[keyName] ?? node.oid : node.oid,
    ))))
    for (const [name, value] of Object.entries(node.properties).sort(([left], [right]) => left.localeCompare(right))) {
      if (value === undefined || value === null) continue
      lines.push(statement(
        subject,
        iri(propertyIri(ontology.id, entity.id, name)),
        typedLiteral(value, entity.propertyMetadata?.[name]?.dataType),
      ))
    }
    lines.push('')
  }

  for (const edge of [...graph.edges].sort((left, right) => left.oid.localeCompare(right.oid))) {
    const relationship = relationships.get(edge.relationshipTypeId!)!
    const predicate = relationshipIri(ontology.id, relationship.id)
    const source = nodeIri(graph.graphModelId, edge.sourceOid)
    const target = nodeIri(graph.graphModelId, edge.targetOid)
    lines.push(statement(source, iri(predicate), iri(target)))
    const resource = edgeIri(graph.graphModelId, edge.oid)
    lines.push(statement(resource, 'a', 'rdf:Statement'))
    lines.push(statement(resource, 'rdf:subject', iri(source)))
    lines.push(statement(resource, 'rdf:predicate', iri(predicate)))
    lines.push(statement(resource, 'rdf:object', iri(target)))
    for (const [name, value] of Object.entries(edge.properties).sort(([left], [right]) => left.localeCompare(right))) {
      if (value === undefined || value === null) continue
      lines.push(statement(
        resource,
        iri(edgePropertyIri(ontology.id, relationship.id, name)),
        typedLiteral(value),
      ))
    }
    lines.push('')
  }
  return lines.join('\n').trimEnd() + '\n'
}

export function knowledgeGraphExportFileName(
  ontology: OntologyContract,
  format: KnowledgeGraphExportFormat,
): string {
  const stem = ontology.displayName.trim().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'fabric-ontology'
  return `${stem}.${format === 'owl-turtle' ? 'owl.ttl' : 'rdf.ttl'}`
}
