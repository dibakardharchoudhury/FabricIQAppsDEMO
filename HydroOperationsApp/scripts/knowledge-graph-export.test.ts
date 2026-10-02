import assert from 'node:assert/strict'
import { performance } from 'node:perf_hooks'
import test from 'node:test'
import { knowledgeGraphExportFileName, serializeNativeKnowledgeGraph } from '../src/services/knowledgeGraphExport.ts'
import type { OntologyContract } from '../src/services/ontologyContract.ts'
import type { OntologyGraph } from '../src/services/ontologyGraph.ts'

const ontology: OntologyContract = {
  id: '22222222-2222-2222-2222-222222222222',
  displayName: 'Hydro Ontology / V2',
  generation: 2,
  entityTypes: [{
    id: 'facility-type',
    name: 'facilities',
    entityIdParts: ['facility-property'],
    properties: { facility_id: 'facility-property', active: 'active-property' },
    propertyMetadata: {
      facility_id: { id: 'facility-property', dataType: 'String' },
      active: { id: 'active-property', dataType: 'Boolean' },
    },
  }],
  relationshipTypes: [{
    id: 'connected-type',
    name: 'connected_to',
    sourceEntityTypeId: 'facility-type',
    targetEntityTypeId: 'facility-type',
    sourceEntityName: 'facilities',
    targetEntityName: 'facilities',
    sourceKeys: ['facility_id'],
    targetKeys: ['facility_id'],
  }],
}

const graph: OntologyGraph = {
  ontologyId: ontology.id,
  graphModelId: '33333333-3333-3333-3333-333333333333',
  graphModelName: 'Hydro native graph',
  nodes: [
    {
      oid: 'node/one',
      labels: ['facilities'],
      entityTypeId: 'facility-type',
      properties: { facility_id: 'F"1', active: true },
    },
    {
      oid: 'node/two',
      labels: ['facilities'],
      entityTypeId: 'facility-type',
      properties: { facility_id: 'F2', active: false },
    },
  ],
  edges: [{
    oid: 'edge/one',
    labels: ['connected_to'],
    relationshipTypeId: 'connected-type',
    sourceOid: 'node/one',
    targetOid: 'node/two',
    properties: { confidence: 0.9 },
  }],
}

test('OWL Turtle export contains verified schema, typed instances, edges and provenance', () => {
  const output = serializeNativeKnowledgeGraph(ontology, graph, 'owl-turtle')
  assert.match(output, /owl:Ontology/)
  assert.match(output, /owl:Class/)
  assert.match(output, /owl:ObjectProperty/)
  assert.match(output, /owl:DatatypeProperty/)
  assert.match(output, /owl:NamedIndividual/)
  assert.match(output, /"true"\^\^xsd:boolean/)
  assert.match(output, /"F\\"1"/)
  assert.match(output, /rdfs:label "F\\"1"/)
  assert.match(output, /rdf:Statement/)
  assert.match(output, /graphModelId/)
  assert.equal(serializeNativeKnowledgeGraph(ontology, graph, 'owl-turtle'), output)
  assert.equal(knowledgeGraphExportFileName(ontology, 'owl-turtle'), 'Hydro-Ontology-V2.owl.ttl')
})

test('RDF Turtle export omits OWL declarations while preserving graph triples', () => {
  const output = serializeNativeKnowledgeGraph(ontology, graph, 'rdf-turtle')
  assert.doesNotMatch(output, /owl:(?:Ontology|Class|ObjectProperty|DatatypeProperty|NamedIndividual)/)
  assert.match(output, /rdfs:Class/)
  assert.match(output, /rdf:Property/)
  assert.match(output, /rdf:subject/)
  assert.equal(knowledgeGraphExportFileName(ontology, 'rdf-turtle'), 'Hydro-Ontology-V2.rdf.ttl')
})

test('export rejects compatibility, mismatched and incomplete native graphs', () => {
  assert.throws(
    () => serializeNativeKnowledgeGraph(ontology, { ...graph, ontologyId: 'different' }, 'owl-turtle'),
    /does not match/,
  )
  assert.throws(
    () => serializeNativeKnowledgeGraph(ontology, {
      ...graph,
      nodes: [{ ...graph.nodes[0], entityTypeId: undefined }],
    }, 'rdf-turtle'),
    /no verified Ontology entity type/,
  )
  assert.throws(
    () => serializeNativeKnowledgeGraph(ontology, {
      ...graph,
      edges: [{ ...graph.edges[0], targetOid: 'missing' }],
    }, 'rdf-turtle'),
    /endpoint outside/,
  )
})

test('maximum visualized graph exports within a bounded client-side budget', () => {
  const nodes = Array.from({ length: 2_000 }, (_, index) => ({
    oid: `node-${index}`,
    labels: ['facilities'],
    entityTypeId: 'facility-type',
    properties: { facility_id: `F-${index}`, active: index % 2 === 0 },
  }))
  const edges = Array.from({ length: 4_000 }, (_, index) => ({
    oid: `edge-${index}`,
    labels: ['connected_to'],
    relationshipTypeId: 'connected-type',
    sourceOid: nodes[index % nodes.length].oid,
    targetOid: nodes[(index + 1) % nodes.length].oid,
    properties: {},
  }))
  const started = performance.now()
  const output = serializeNativeKnowledgeGraph(
    ontology,
    { ...graph, nodes, edges },
    'owl-turtle',
  )
  const elapsed = performance.now() - started
  assert.ok(output.length > 1_000_000)
  assert.ok(elapsed < 5_000, `Expected export under 5 seconds, received ${elapsed.toFixed(1)}ms`)
})
