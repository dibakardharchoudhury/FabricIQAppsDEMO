import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import {
  canonicalJson, columnPeers, fingerprint, parseContract, preview, reconcileSources, suggestions, updateMapping, validateContract, type Contract,
} from '../src/master-data/contract.ts'
import { exampleContract, exampleSamples } from '../src/master-data/example.ts'
import { parseOntologyContract, type DefinitionPart } from '../src/services/ontologyContract.ts'
import { definitionFingerprint, ontologyIssues } from '../src/master-data/ontologyValidation.ts'
import viteConfig from '../vite.config.ts'

function approved(): Contract {
  const c = exampleContract()
  c.ontology.definitionHash = 'a'.repeat(64)
  c.mappings.forEach(m => { m.reviewed = true })
  c.mappings.push({ source: 'crm', column: 'loyalty_tier', target: null, transform: 'identity', reason: 'Not part of this approved product.', reviewed: true })
  return c
}

test('new columns, review gaps and missing live fingerprint block release', () => {
  const codes = validateContract(exampleContract()).map(i => i.code)
  assert.ok(codes.includes('new_column'))
  assert.ok(codes.includes('review'))
  assert.ok(codes.includes('ontology'))
  assert.deepEqual(validateContract(approved()), [])
})

test('multi-entry build preserves the canonical deployer index asset name and Windows-safe paths', () => {
  const inputs = viteConfig.build.rollupOptions.input
  assert.equal(inputs.index, fileURLToPath(new URL('../index.html', import.meta.url)))
  assert.ok(inputs.masterData.endsWith('master-data.html'))
  assert.deepEqual(Object.keys(inputs), ['index', 'masterData'])
})

test('contract import rejects unknown fields, wrong versions, legacy ontology and malformed nested data', () => {
  assert.deepEqual(parseContract(approved()), approved())
  for (const patch of [{ formatVersion: 2 }, { unknown: true }, { constructor: true }, { sources: null }, { ontology: { generation: 1 } }]) {
    assert.throws(() => parseContract({ ...approved(), ...patch }))
  }
  const c = approved()
  c.mappings[0].reviewed = 'yes' as unknown as boolean
  assert.throws(() => parseContract(c))
  const wrongScalar = approved()
  wrongScalar.sources[0].columns[0].type = ['string'] as unknown as 'string'
  assert.throws(() => parseContract(wrongScalar))
})

test('live v2 ontology validation follows explicit property bindings, not similar names or guessed joins', async () => {
  const c = approved()
  const part = (path: string, value: string): DefinitionPart => ({ path, payload: Buffer.from(value).toString('base64'), payloadType: 'InlineBase64' })
  const parts: DefinitionPart[] = c.entities.map(e => part(`entities/${e.ontologyEntity}.tmdl`, [
    `entity ${e.ontologyEntity}`, `    backingTable: ${e.goldTable}`, `    keyProperty: ${e.properties.find(p => p.name === e.key)!.ontologyProperty}`,
    ...e.properties.flatMap(p => [
      `    property ${p.ontologyProperty}`, `        dataType: ${p.type}`,
      '        backingConfiguration', `            valueColumn: ${e.goldTable}.physical_${p.name}`,
    ]),
  ].join('\n')))
  parts.push(part('relationships.tmdl', `relationship order_customer_physical
    fromColumn: sales_order.physical_customer_id
    toColumn: customer.physical_customer_id
    fromCardinality: many
    toCardinality: one
`), part('entityRelationships.tmdl', `entityRelationship OrderCustomer
    fromEntity: SalesOrder
    toEntity: Customer
    backingConfiguration
        relationship: order_customer_physical
`))
  const live = parseOntologyContract(c.ontology.itemId, 'Customer ontology', { definition: { parts } }, 2)
  assert.deepEqual(ontologyIssues(c, live), [])
  assert.equal(await definitionFingerprint(parts), await definitionFingerprint([...parts].reverse()))
  assert.throws(() => parseOntologyContract('id', 'Legacy', { definition: { parts } }, 1))
  const mutated = structuredClone(live)
  mutated.relationshipTypes[0].targetKeys = ['guessed_customer_id']
  assert.ok(ontologyIssues(c, mutated).some(i => i.code === 'ontology_relationship'))
  mutated.entityTypes[0].entityIdParts = ['different_key']
  assert.ok(ontologyIssues(c, mutated).some(i => i.code === 'ontology_key'))
  const newProperty = approved()
  newProperty.entities[0].properties.push({
    name: 'loyalty_tier', ontologyProperty: 'undeclared_loyalty_tier', label: 'Loyalty tier', required: false,
    type: 'string', strategy: 'require_equal', priority: [],
  })
  assert.ok(ontologyIssues(newProperty, live).some(i => i.code === 'ontology_property'))
})

test('explicit source priority resolves names but unequal email blocks entire publication, not just a column', () => {
  const result = preview(approved(), exampleSamples)
  assert.ok(result.conflicts.some(c => c.includes('customer/C-1002/email')))
  assert.ok(result.conflicts.some(c => c.includes('orphan foreign key')))
  assert.equal(result.rows.customer.length, 2)
  assert.equal(result.rows.customer.find(row => row.customer_id === 'C-1001')?.customer_name, 'Northwind Energy')
  assert.equal(result.rows.customer.find(row => row.customer_id === 'C-1001')?.email, 'hello@northwind.example')
  assert.deepEqual(result.lineage.find(l => l.key === 'C-1001' && l.property === 'customer_name'), {
    entity: 'customer', key: 'C-1001', property: 'customer_name', source: 'crm', column: 'customer',
  })
})

test('priority uses first non-null, not latest row order, and preserves false and zero', () => {
  const c = approved()
  const name = c.entities[0].properties.find(p => p.name === 'customer_name')!
  name.priority = ['erp', 'crm']
  const samples = structuredClone(exampleSamples)
  samples.erp[0].customer = null
  assert.equal(preview(c, samples).rows.customer[0].customer_name, 'Northwind Energy')
  samples.erp[0].customer = 'Authoritative ERP name'
  assert.equal(preview(c, samples).rows.customer[0].customer_name, 'Authoritative ERP name')
  for (const [type, value] of [['boolean', false], ['int64', 0]] as const) {
    c.entities[0].properties.push({ name: `test_${type}`, ontologyProperty: `test_${type}`, label: type, type, required: true, strategy: 'require_equal', priority: [] })
    c.sources[0].columns.push({ name: `test_${type}`, type })
    c.mappings.push({ source: 'crm', column: `test_${type}`, target: `test_${type}`, reason: 'Test scalar', reviewed: true, transform: 'identity' })
    samples.crm.forEach(row => { row[`test_${type}`] = value })
  }
  const row = preview(c, samples).rows.customer.find(row => row.customer_id === 'C-1001')!
  assert.equal(row.test_boolean, false)
  assert.equal(row.test_int64, 0)
})

test('same customer label never implies record identity or a fuzzy match', () => {
  const c = approved()
  c.sources[1].keyNamespace = 'sap_local_customer_number'
  assert.ok(validateContract(c).some(i => i.code === 'identity'))
  const samples = structuredClone(exampleSamples)
  samples.erp[0].customer_id = 'SAP-77'
  const result = preview(approved(), samples)
  assert.ok(result.rows.customer.some(row => row.customer_id === 'SAP-77'))
  assert.ok(result.rows.customer.some(row => row.customer_id === 'C-1001'))
})

test('duplicate, null, blank keys, invalid types, missing source and orphan foreign keys fail closed', () => {
  const c = approved()
  for (const key of [null, '', '  ']) {
    const samples = structuredClone(exampleSamples)
    samples.crm[0].customer_id = key
    assert.ok(preview(c, samples).conflicts.some(c => /business key/.test(c)))
  }
  const duplicated = structuredClone(exampleSamples)
  duplicated.crm.push(duplicated.crm[0])
  assert.ok(preview(c, duplicated).conflicts.some(c => /duplicate business key/.test(c)))
  const wrongType = structuredClone(exampleSamples)
  wrongType.crm[0].customer = 123
  assert.ok(preview(c, wrongType).conflicts.some(c => /invalid or missing/.test(c)))
  const missing = structuredClone(exampleSamples)
  delete missing.erp
  assert.ok(preview(c, missing).conflicts.some(c => /unavailable/.test(c)))
})

test('schema drift reconciliation preserves unchanged decisions and invalidates binding and type changes', () => {
  const c = approved()
  const inventory = { sources: structuredClone(c.sources) }
  inventory.sources[0].columns.push({ name: 'new_attribute', type: 'string' })
  let next = reconcileSources(c, inventory)
  assert.equal(next.mappings[0].reviewed, true)
  assert.ok(validateContract(next).some(i => i.code === 'new_column'))
  inventory.sources[0].columns[1].type = 'int64'
  next = reconcileSources(c, inventory)
  assert.equal(next.mappings.find(m => m.source === 'crm' && m.column === 'customer')?.reviewed, false)
  inventory.sources[1].table = 'changed_binding'
  next = reconcileSources(c, inventory)
  assert.ok(next.mappings.filter(m => m.source === 'erp').every(m => !m.reviewed))
  inventory.sources[1].columns = inventory.sources[1].columns.filter(c => c.name !== 'email')
  assert.ok(validateContract(reconcileSources(c, inventory)).some(i => i.code === 'removed_column'))
})

test('priority omissions, invalid mappings, key normalization, duplicate joins and relationship cycles are rejected', () => {
  const mutations: ((c: Contract) => void)[] = [
    c => { c.entities[0].properties[1].priority = ['crm'] },
    c => { c.mappings[0].transform = 'trim' },
    c => { c.mappings[0].target = null },
    c => { c.mappings[1].target = 'does_not_exist' },
    c => { c.mappings.push({ ...c.mappings[0] }) },
    c => { c.relationships.push({ ...c.relationships[0], name: 'cycle' }) },
    c => { c.sources[0].table = 'unsafe; DROP TABLE gold' },
    c => { c.entities[0].properties[1].strategy = 'require_equal' },
  ]
  for (const mutate of mutations) {
    const c = approved(); mutate(c)
    assert.ok(validateContract(c).length > 0)
  }
})

test('suggestions are hints only, and updating decisions does not duplicate attachments', () => {
  const c = approved()
  assert.deepEqual(suggestions(c, c.sources[0], 'CUSTOMER_NAME'), ['customer_name'])
  assert.deepEqual(suggestions(c, c.sources[0], 'loyalty_tier'), [])
  const updated = updateMapping(c, { ...c.mappings[0], reason: 'Updated business rationale' })
  assert.equal(updated.mappings.length, c.mappings.length)
  assert.equal(updated.mappings.at(-1)?.reason, 'Updated business rationale')
})

test('unmapped similarly named source columns are surfaced as potential overlap, never auto-mapped', () => {
  const c = approved()
  c.mappings = c.mappings.filter(m => m.column !== 'customer')
  assert.deepEqual(columnPeers(c, c.sources[0], 'customer'), [{ source: 'erp', column: 'customer' }])
  assert.equal(c.mappings.some(m => m.column === 'customer'), false)
  assert.ok(validateContract(c).some(i => i.code === 'new_column'))
})

test('canonical fingerprints match the authoritative Python runner, including Unicode and property ordering', async () => {
  const c = approved()
  c.entities[0].label = "Kunde Øst's master"
  const raw = canonicalJson(c)
  const result = spawnSync('python3', ['-c', `
import json, sys
from MasterDataManagement.contract import fingerprint, load_approved
value = json.load(sys.stdin)
digest = fingerprint(value)
load_approved(json.dumps(value), digest)
print(digest)
`], { input: raw, encoding: 'utf8', cwd: new URL('../../', import.meta.url) })
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.stdout.trim(), await fingerprint(c))
})
