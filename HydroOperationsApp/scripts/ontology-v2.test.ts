import assert from 'node:assert/strict'
import test from 'node:test'
import { parseOntologyContract, type DefinitionPart } from '../src/services/ontologyContract.ts'
import { selectOntology, V2_GRAPH_UNAVAILABLE } from '../src/services/ontologyArtifactDiscovery.ts'
import { createOntologyCache } from '../src/services/ontologyCache.ts'
import { discoverOntology } from '../src/services/ontologyDiscovery.ts'
import { buildKnowledgeGraph } from '../src/ui-shared/knowledgeGraphModel.ts'

const tmdl = (path: string, text: string): DefinitionPart => ({ path, payload: Buffer.from(text).toString('base64'), payloadType: 'InlineBase64' })
const parse = (parts: DefinitionPart[], generation: unknown = 2) => parseOntologyContract('ontology-v2', 'Hydro v2', { definition: { parts } }, generation)
const facility = tmdl('entities/facilities.tmdl', `/// Station documentation
entity facilities
    backingTable: silver_facilities
    keyProperty: facility_id
    property facility_id
        dataType: string
        backingConfiguration
            valueColumn: silver_facilities.facility_id
`)
const system = tmdl('entities/hydro#systems.tmdl', `entity hydro#systems
    lineageTag: system-lineage
    backingTable: silver_systems
    keyProperty: system_id
    property system_id
        dataType: string
        lineageTag: system-key
    property facility_id
        dataType: string
        backingConfiguration
            valueColumn: silver_systems.facility_id
`)
const physical = tmdl('relationships.tmdl', `relationship systems_facilities
    fromColumn: silver_systems.facility_id
    toColumn: silver_facilities.facility_id
relationship unrelated
    fromColumn: unrelated.id
    toColumn: unrelated2.id
`)
const semantic = tmdl('entityRelationships.tmdl', `/// A semantic edge, not a TOM table edge.
entityRelationship 'hydro#Systems in facilities'
    label: Systems inside facilities
    fromEntity: 'hydro#systems'
    toEntity: default#facilities
    backingConfiguration
        relationship: systems_facilities
`)
const hydro = [facility, system, physical, semantic]

test('v2 definitions without model refs parse namespaces, optional lineage, keys and only semantic edges', () => {
  const contract = parse(hydro, 2)
  assert.equal(contract.generation, 2)
  assert.equal(contract.entityTypes[0].namespace, 'default')
  assert.equal(contract.entityTypes[0].id, 'facilities')
  assert.deepEqual(contract.entityTypes[0].entityIdParts, ['facilities.facility_id'])
  assert.equal(contract.entityTypes[1].name, 'hydro#systems')
  assert.equal(contract.entityTypes[1].localName, 'systems')
  assert.deepEqual(contract.entityTypes[1].entityIdParts, ['system-key'])
  assert.equal(contract.relationshipTypes.length, 1)
  assert.equal(contract.relationshipTypes[0].label, 'Systems inside facilities')
  assert.equal(contract.relationshipTypes[0].sourceEntityTypeId, 'system-lineage')
  assert.deepEqual(contract.relationshipTypes[0].sourceKeys, ['facility_id'])
  assert.deepEqual(contract.relationshipTypes[0].targetKeys, ['facility_id'])
  assert.equal(parse([facility, system, physical]).relationshipTypes.length, 0)
})

test('CRLF tabs Unicode quoted names apostrophes descriptions and resourceLink siblings', () => {
  const contract = parse([tmdl("entities/plant#Station's sensors.tmdl", `/// Čidlo
entity 'plant#Station''s sensors'
\tbackingTable: 'Silver sensors'
\tkeyProperty: 'Sensor id'
\tproperty 'Sensor id'
\t\tdataType: string
\t\tbackingConfiguration
\t\t\tvalueColumn: 'Silver sensors'.'Sensor id'
\t/// Resource links are siblings, never property children.
\tresourceLink
\t\titem
\t\t\tworkspaceId: workspace
\t\t\titemId: dashboard
`.replaceAll('\n', '\r\n'))])
  assert.equal(contract.entityTypes[0].name, "plant#Station's sensors")
  assert.deepEqual(contract.entityTypes[0].propertyMetadata?.['Sensor id'].backingConfiguration?.valueColumn, { table: 'Silver sensors', column: 'Sensor id' })
  assert.match(contract.warnings!.join(' '), /resource links/)
})

test('retains Any primitive timeseries and complex structured timeseries metadata without claiming evaluation', () => {
  const contract = parse([tmdl('entities/signals.tmdl', `entity signals
    property Anything
        dataType: Any
    property Power
        dataType: TimeSeries<double>
        backingConfiguration
            type: timeSeries
            orderingColumn: readings.timestamp
            valueColumn: readings.power
    property Weather
        dataType: complex
        complexDataType = {"kind":"timeSeries","elementType":{"kind":"struct","fields":[{"name":"Speed","type":{"kind":"primitive","dataType":"double"}}]}}
        backingConfiguration
            type: timeSeries
            orderingColumn: readings.timestamp
            valueBackingConfiguration = {"type":"struct","fields":[{"name":"Speed","backingConfiguration":{"type":"default","valueColumn":{"table":"readings","column":"speed"}}}]}
`)])
  const metadata = contract.entityTypes[0].propertyMetadata!
  assert.equal(metadata.Anything.dataType, 'Any')
  assert.equal(metadata.Power.dataType, 'TimeSeries<double>')
  assert.equal(metadata.Weather.complexDataType?.kind, 'timeSeries')
  assert.equal((metadata.Weather.backingConfiguration?.valueBackingConfiguration as { type: string }).type, 'struct')
  assert.match(contract.warnings!.join(' '), /metadata.*not evaluated/)
  assert.match(contract.warnings!.join(' '), /live readings still use/)
})

test('retains complex struct field backing metadata and rejects unknown recursive types', () => {
  const text = `entity signals
    property Location
        dataType: complex
        complexDataType = {"kind":"struct","fields":[{"name":"Altitude","type":{"kind":"primitive","dataType":"double"}}]}
        backingConfiguration
            type: struct
            fields = [{"name":"Altitude","backingConfiguration":{"type":"default","valueColumn":{"table":"signals","column":"altitude"}}}]
`
  const contract = parse([tmdl('entities/signals.tmdl', text)])
  assert.equal(contract.entityTypes[0].propertyMetadata?.Location.backingConfiguration?.type, 'struct')
  assert.throws(() => parse([tmdl('entities/signals.tmdl', text.replace('"kind":"struct"', '"kind":"array"'))]), /root kind/)
  assert.throws(() => parse([tmdl('entities/signals.tmdl', text.replace('"kind":"primitive"', '"kind":"array"'))]), /unsupported complex kind/)
  assert.throws(() => parse([tmdl('entities/signals.tmdl', text.replace('"type":"default"', '"type":"unknown"'))]), /unsupported backing type/)
})

test('projects Hydro signal master additional Eventhouse backing table and bound/unbound timeseries', () => {
  const signals = tmdl('entities/signal_master.tmdl', `entity signal_master
    backingTable: signal_master
    keyProperty: opcua_node_id
    additionalBackingTable
        table: OPCUAEvents
        relationship: signal_master_OPCUAEvents
    property opcua_node_id
        dataType: string
        backingConfiguration
            valueColumn: signal_master.opcua_node_id
    property is_active
        dataType: boolean
    property event_time
        dataType: TimeSeries<dateTime>
    property value
        dataType: TimeSeries<double>
        backingConfiguration
            type: timeSeries
            orderingColumn: OPCUAEvents.event_time
            valueColumn: OPCUAEvents.value
    property quality
        dataType: TimeSeries<string>
        backingConfiguration
            type: timeSeries
            orderingColumn: OPCUAEvents.event_time
            valueColumn: OPCUAEvents.quality
`)
  const joins = tmdl('relationships.tmdl', `relationship signal_master_OPCUAEvents
    fromColumn: signal_master.opcua_node_id
    toColumn: OPCUAEvents.opcua_node_id
`)
  const contract = parse([signals, joins])
  const signal = contract.entityTypes[0]
  assert.deepEqual(signal.additionalBackingTables, [{ table: 'OPCUAEvents', relationship: 'signal_master_OPCUAEvents' }])
  assert.equal(signal.propertyMetadata!.event_time.backingConfiguration, undefined)
  assert.deepEqual(signal.propertyMetadata!.value.backingConfiguration!.orderingColumn, { table: 'OPCUAEvents', column: 'event_time' })
  assert.equal(contract.relationshipTypes.length, 0)
  assert.throws(() => parse([signals]), /additional backing relationship.*not found/)
})

test('minimal database-only v2 is explicitly empty; physical-only and unknown definitions are not silently empty', () => {
  const contract = parse([tmdl('database.tmdl', 'database\n    compatibilityLevel: 1000000')], 2)
  assert.equal(contract.entityTypes.length, 0)
  assert.match(contract.warnings!.join(' '), /contains no entities/)
  assert.throws(() => parse([physical]), /no entities/)
  assert.throws(() => parse([]), /no supported/)
  assert.throws(() => parse([tmdl('database.tmdl', 'invalid')]), /no entities/)
})

test('live empty v2 CRLF readback with synthesized namespace and refs stays explicitly empty', () => {
  const contract = parse([
    tmdl('namespaces/default.tmdl', 'namespace default\r\n\tlineageTag: default\r\n\r\n'),
    tmdl('model.tmdl', 'model Model\r\n\r\nref namespace default\r\n\r\n'),
    tmdl('database.tmdl', '/// Empty ontology description\r\ndatabase\r\n\tcompatibilityLevel: 1000000\r\n'),
  ], 2)
  assert.equal(contract.generation, 2)
  assert.deepEqual(contract.entityTypes, [])
  assert.deepEqual(contract.relationshipTypes, [])
  assert.match(contract.warnings!.join(' '), /contains no entities/)
})

test('rejects mixed generations generation mismatches duplicate parts malformed payloads and unsupported transport', () => {
  const legacy = tmdl('EntityTypes/one/definition.json', '{"name":"one","id":"one"}')
  assert.throws(() => parse([facility, legacy]), /Replace the existing v1 Ontology/)
  assert.throws(() => parse([facility], 1), /Replace the existing v1 Ontology/)
  assert.throws(() => parse([legacy], 2), /Replace the existing v1 Ontology/)
  for (const generation of [undefined, null, '2', 3]) {
    assert.throws(() => parseOntologyContract('id', 'name', { definition: { parts: [facility] } }, generation), /could not be verified/)
  }
  assert.throws(() => parse([facility, facility]), /duplicate part paths/)
  assert.throws(() => parse([{ ...facility, payload: '!!!!' }]), /invalid Base64/)
  assert.throws(() => parse([{ ...facility, payloadType: 'External' }]), /InlineBase64/)
  assert.throws(() => parse([tmdl('EntityTypes/one/definition.json', '{broken')]), /Replace the existing v1 Ontology/)
})

for (const [title, body, expected] of [
  ['duplicate scalar key', 'keyProperty: id\n    keyProperty: id', /duplicate keyProperty/],
  ['composite key', 'keyProperty: id, other', /invalid name/],
  ['missing key property', 'keyProperty: missing', /does not reference/],
  ['unknown type', 'property id\n        dataType: geography', /unsupported.*dataType/],
  ['missing complex JSON', 'property id\n        dataType: complex', /requires complexDataType/],
  ['malformed complex JSON', 'property id\n        dataType: complex\n        complexDataType = {broken}', /JSON object/],
  ['multiline type expression', 'property id\n        dataType: complex\n        complexDataType =\n            {"kind":"struct"}', /single-line JSON/],
  ['unsupported property projection', 'property id\n        dataType: string\n        expression = 1', /unsupported property construct/],
  ['incomplete timeseries', 'property id\n        dataType: TimeSeries<double>\n        backingConfiguration\n            type: timeSeries\n            valueColumn: readings.value', /orderingColumn/],
  ['conflicting timeseries values', 'property id\n        dataType: TimeSeries<double>\n        backingConfiguration\n            type: timeSeries\n            orderingColumn: readings.time\n            valueColumn: readings.value\n            valueBackingConfiguration = {"type":"struct"}', /exactly one/],
  ['unrecognized entity member', 'compositeKey id', /unsupported entity construct/],
] as const) {
  test(`rejects ${title} explicitly`, () => assert.throws(() => parse([tmdl('entities/test.tmdl', `entity test\n    ${body}`)]), expected))
}

test('rejects unresolved semantic endpoints wrong declarations duplicate names and physical binding mismatch', () => {
  assert.throws(() => parse([facility, semantic]), /fromEntity.*does not resolve/)
  assert.throws(() => parse([facility, tmdl('entityRelationships.tmdl', 'relationship wrong')]), /expected an entityRelationship/)
  assert.throws(() => parse([facility, tmdl('entities/other.tmdl', 'entity facilities')]), /part path/)
  assert.throws(() => parse([facility, tmdl('entities/default#facilities.tmdl', 'entity default#facilities')]), /duplicate identity/)
  assert.throws(() => parse([facility, system, semantic]), /backing relationship.*not found/)
  assert.throws(() => parse([facility, system, semantic, tmdl('relationships.tmdl', 'relationship systems_facilities\n    fromColumn: wrong.id\n    toColumn: wrong2.id')]), /tables do not match/)
})

test('junction and unbound semantic edges remain metadata not invented compatibility joins', () => {
  const contract = parse([facility, system, tmdl('entityRelationships.tmdl', `entityRelationship junction
    fromEntity: hydro#systems
    toEntity: facilities
    backingConfiguration
        type: table
        table: system_facility
        fromRelationship: from_link
        toRelationship: to_link
entityRelationship unbound
    fromEntity: hydro#systems
    toEntity: facilities
`)])
  assert.equal(contract.relationshipTypes.length, 2)
  assert.ok(contract.relationshipTypes.every(relationship => relationship.compatibilityUnsupported))
  assert.match(contract.warnings!.join(' '), /not rendered/)
})

const graphInput = {
  facilities: [{ facility_id: 'F1', facility_name: 'Station' }],
  systems: [{ system_id: 'S1', facility_id: 'F1' }],
  equipment: [], instruments: [], telemetry: [], workOrders: [], inspections: [], notifications: [], models: [],
}
test('v2 custom namespace joins Hydro backing tables; absent semantic edges and ambiguous roles do not invent topology', () => {
  const ontology = parse(hydro)
  const graph = buildKnowledgeGraph({ ...graphInput, ontology })
  assert.equal(graph.edges.length, 1)
  assert.equal(graph.edges[0].ontologyRelationshipId, 'hydro#Systems in facilities')
  assert.equal(buildKnowledgeGraph({ ...graphInput, ontology: parse([facility, system, physical]) }).edges.length, 0)
  const ambiguous = { ...ontology, entityTypes: [...ontology.entityTypes, { ...ontology.entityTypes[1], id: 'other', name: 'other#systems' }] }
  assert.equal(buildKnowledgeGraph({ ...graphInput, ontology: ambiguous }).edges.length, 0)
  const wrongJoin = { ...ontology, relationshipTypes: ontology.relationshipTypes.map(item => ({ ...item, sourceKeys: ['system_id'] })) }
  assert.equal(buildKnowledgeGraph({ ...graphInput, ontology: wrongJoin }).edges.length, 0)
})

test('configured Ontology name cannot fall back to an unrelated item', () => {
  assert.equal(selectOntology([{ id: 'wrong', type: 'Ontology', displayName: 'Other' }], 'Requested'), undefined)
  assert.match(V2_GRAPH_UNAVAILABLE, /unrelated workspace Graph Models will not be queried/)
})

const artifacts = [
  { id: 'semantic', type: 'Ontology', displayName: 'Hydro v2' },
  { id: 'unrelated', type: 'GraphModel', displayName: 'Hydro v2 graph' },
]
test('discovery checks generation and v2 definition without querying an unrelated GraphModel', async () => {
  let sampled = false
  const reader = {
    metadata: async () => ({ properties: { generation: 2 } }),
    definition: async () => ({ definition: { parts: hydro } }),
    labels: async () => { sampled = true; return new Set(['facilities']) },
  }
  const result = await discoverOntology(artifacts, undefined, reader)
  assert.equal(result.ontologyGeneration, 2)
  assert.equal('graphModelId' in result, false)
  assert.equal(result.graphUnavailableReason, V2_GRAPH_UNAVAILABLE)
  assert.equal(sampled, false)
  const elided = await discoverOntology(artifacts, undefined, { ...reader, metadata: async () => ({}) })
  assert.match(elided.ontologyError!, /could not be verified/)
  assert.equal(elided.ontologyGeneration, undefined)
  assert.equal(sampled, false)
})

test('discovery preserves ontology identity while surfacing definition metadata and generation errors', async () => {
  const reader = {
    metadata: async () => ({ properties: { generation: 2 } }),
    definition: async () => ({ definition: { parts: hydro } }),
    labels: async () => { throw new Error('must never sample') },
  }
  const denied = await discoverOntology(artifacts, undefined, { ...reader, definition: async () => { throw new Error('403 definition denied') } })
  assert.equal(denied.ontologyId, 'semantic')
  assert.match(denied.ontologyError!, /403 definition denied/)
  assert.equal('graphModelId' in denied, false)
  const malformed = await discoverOntology(artifacts, undefined, { ...reader, definition: async () => ({ definition: { parts: [tmdl('entities/facilities.tmdl', 'not an entity')] } }) })
  assert.match(malformed.ontologyError!, /entity declaration/)
  const mismatch = await discoverOntology(artifacts, undefined, { ...reader, metadata: async () => ({ properties: { generation: 1 } }) })
  assert.match(mismatch.ontologyError!, /Replace the existing v1 Ontology/)
  const future = await discoverOntology(artifacts, undefined, { ...reader, metadata: async () => ({ properties: { generation: 3 } }) })
  assert.match(future.ontologyError!, /could not be verified/)
  const noOntology = await discoverOntology(artifacts.slice(1), undefined, reader)
  assert.match(noOntology.ontologyError!, /No Ontology v2/)
  const missing = await discoverOntology(artifacts, 'Missing', reader)
  assert.match(missing.ontologyError!, /configured name was not found/)
})

test('v1 discovery fails before definition or graph reads even with matching sole or multiple graphs', async () => {
  let read = false
  const reader = {
    metadata: async () => ({ properties: { generation: 1 } }),
    definition: async () => { read = true; throw new Error('must not read') },
    labels: async () => { read = true; throw new Error('must not sample') },
  }
  assert.match((await discoverOntology(artifacts, undefined, reader)).ontologyError!, /Replace the existing v1 Ontology/)
  const multiple = await discoverOntology([...artifacts, { id: 'second', type: 'GraphModel', displayName: 'Second' }], undefined, reader)
  assert.match(multiple.ontologyError!, /Replace the existing v1 Ontology/)
  assert.equal('graphModelId' in multiple, false)
  assert.equal(read, false)
})

test('missing ontology never invents governed compatibility edges', () => {
  assert.equal(buildKnowledgeGraph(graphInput).edges.length, 0)
})

test('cache failure and absence invalidate successful data; recovery reloads rather than resurrecting stale data', async () => {
  const cache = createOntologyCache<string>(100_000)
  assert.equal(await cache.read('one', false, async () => 'old'), 'old')
  await assert.rejects(cache.read('one', true, async () => { throw new Error('403 forbidden') }), /403/)
  assert.equal(await cache.read('one', false, async () => 'new'), 'new')
  assert.equal(await cache.read('one', true, async () => null), null)
  assert.equal(await cache.read('one', false, async () => 'after-null'), 'after-null')
})

test('cache isolates artifact ids, deduplicates requests and rejects invalidated in-flight results', async () => {
  const cache = createOntologyCache<string>(100_000)
  let finish!: (value: string) => void
  const pending = cache.read('one', true, () => new Promise<string>(resolve => { finish = resolve }))
  const duplicate = cache.read('one', true, async () => { throw new Error('must not execute') })
  await Promise.resolve()
  cache.clear()
  finish('stale')
  await assert.rejects(pending, /discovery changed/)
  await assert.rejects(duplicate, /discovery changed/)
  assert.equal(await cache.read('two', false, async () => 'current'), 'current')
  assert.equal(await cache.read('one', false, async () => 'reloaded'), 'reloaded')
})
