export type ScalarType = 'string' | 'int64' | 'boolean'
export type Transform = 'identity' | 'trim' | 'lower' | 'upper'
export type Property = {
  name: string
  ontologyProperty: string
  label: string
  type: ScalarType
  required: boolean
  strategy: 'require_equal' | 'source_priority'
  priority: string[]
}
export type Entity = { id: string; ontologyEntity: string; label: string; goldTable: string; key: string; properties: Property[] }
export type Source = {
  id: string
  label: string
  entity: string
  workspaceId: string
  lakehouseId: string
  schema: string
  table: string
  keyColumn: string
  keyNamespace: string
  columns: { name: string; type: ScalarType }[]
}
export type Mapping = {
  source: string
  column: string
  target: string | null
  transform: Transform
  reason: string
  reviewed: boolean
}
export type Relationship = { name: string; ontologyRelationship: string; from: string; column: string; to: string }
export type Contract = {
  formatVersion: 1
  name: string
  ontology: { workspaceId: string; itemId: string; generation: 2; definitionHash: string }
  entities: Entity[]
  sources: Source[]
  mappings: Mapping[]
  relationships: Relationship[]
}
export type Samples = Record<string, Record<string, unknown>[]>
export type Issue = { code: string; message: string }
const identifier = /^[a-z][a-z0-9_]{0,62}$/
const guid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i
const scalarTypes = ['string', 'int64', 'boolean']
const transforms = ['identity', 'trim', 'lower', 'upper']

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected a JSON object.')
  return value as Record<string, unknown>
}
function shape(value: unknown, fields: Record<string, (value: unknown) => boolean>): boolean {
  const record = object(value)
  return Object.keys(record).every(key => Object.hasOwn(fields, key)) && Object.entries(fields).every(([key, check]) => check(record[key]))
}
const text = (value: unknown): value is string => typeof value === 'string' && value.length <= 2048
const strings = (value: unknown) => Array.isArray(value) && value.every(text)
const array = (check: (value: unknown) => boolean) => (value: unknown) => Array.isArray(value) && value.length <= 2000 && value.every(check)
const scalar = (value: unknown) => typeof value === 'string' && scalarTypes.includes(value)
const column = (value: unknown) => shape(value, { name: text, type: scalar })
const property = (value: unknown) => shape(value, {
  name: text, ontologyProperty: text, label: text, type: scalar, required: v => typeof v === 'boolean',
  strategy: v => typeof v === 'string' && ['require_equal', 'source_priority'].includes(v), priority: strings,
})
const source = (value: unknown) => shape(value, {
  id: text, label: text, entity: text, workspaceId: text, lakehouseId: text, schema: text, table: text,
  keyColumn: text, keyNamespace: text, columns: array(column),
})

export function parseContract(value: unknown): Contract {
  if (!shape(value, {
    formatVersion: v => v === 1, name: text,
    ontology: v => shape(v, { workspaceId: text, itemId: text, generation: g => g === 2, definitionHash: text }),
    sources: array(source),
    entities: array(v => shape(v, { id: text, ontologyEntity: text, label: text, goldTable: text, key: text, properties: array(property) })),
    mappings: array(v => shape(v, {
      source: text, column: text, target: t => t === null || text(t), transform: t => typeof t === 'string' && transforms.includes(t),
      reason: text, reviewed: t => typeof t === 'boolean',
    })),
    relationships: array(v => shape(v, { name: text, ontologyRelationship: text, from: text, column: text, to: text })),
  })) throw new Error('Unsupported or incomplete contract. Use a formatVersion 1 contract; unknown fields are rejected.')
  return value as Contract
}

export function validateContract(contract: Contract): Issue[] {
  const issues: Issue[] = []
  const add = (code: string, message: string) => issues.push({ code, message })
  const unique = (values: string[], label: string) => {
    if (new Set(values.map(value => value.toLowerCase())).size !== values.length) add('duplicate', `${label} must be unique, ignoring case.`)
  }
  const name = (value: string, label: string) => {
    if (!identifier.test(value)) add('identifier', `${label}: use lower_snake_case (maximum 63 characters).`)
  }
  const label = (value: string, context: string) => {
    if (!value.trim() || value !== value.trim() || [...value].some(char => char.charCodeAt(0) < 32)) add('label', `${context}: use a nonempty label without leading/trailing whitespace or control characters.`)
  }
  label(contract.name, 'Product name')
  if (contract.name.length > 255) add('name', 'Product names are limited to 255 characters.')
  if (!guid.test(contract.ontology.workspaceId) || !guid.test(contract.ontology.itemId)
    || !/^[a-f0-9]{64}$/.test(contract.ontology.definitionHash)) add('ontology', 'Bind a live generation-2 ontology and its definition fingerprint.')
  if (!contract.entities.length || !contract.sources.length) add('empty', 'At least one entity and Silver source are required.')
  unique(contract.entities.map(e => e.id), 'Entity identifiers')
  unique(contract.entities.map(e => e.ontologyEntity), 'Ontology entities')
  unique(contract.entities.map(e => e.goldTable), 'Gold table names')
  unique(contract.entities.map(e => e.label), 'Semantic table labels')
  unique(contract.sources.map(s => s.id), 'Source identifiers')
  unique(contract.sources.map(s => `${s.workspaceId}/${s.lakehouseId}/${s.schema}/${s.table}`), 'Physical source tables')
  unique(contract.mappings.map(m => `${m.source}/${m.column}`), 'Column decisions')
  unique(contract.relationships.map(r => r.name), 'Relationship names')
  for (const entity of contract.entities) {
    name(entity.id, 'Entity'); name(entity.goldTable, 'Gold table')
    label(entity.label, entity.id); label(entity.ontologyEntity, `${entity.id} ontology entity`)
    unique(entity.properties.map(p => p.name), `${entity.id} properties`)
    unique(entity.properties.map(p => p.ontologyProperty), `${entity.id} ontology properties`)
    unique(entity.properties.map(p => p.label), `${entity.id} semantic labels`)
    const key = entity.properties.find(p => p.name === entity.key)
    if (!key || key.type !== 'string' || !key.required || key.strategy !== 'require_equal') add('key', `${entity.id}: key must be a required string with require_equal.`)
    const sources = contract.sources.filter(s => s.entity === entity.id)
    if (!sources.length) add('source', `${entity.id}: no Silver source is bound.`)
    if (new Set(sources.map(s => s.keyNamespace)).size > 1 || sources.some(s => !s.keyNamespace.trim())) add('identity', `${entity.id}: sources need an explicitly shared key namespace. Apply a verified crosswalk upstream for local IDs.`)
    for (const p of entity.properties) {
      name(p.name, 'Canonical column')
      label(p.label, `${entity.id}.${p.name}`); label(p.ontologyProperty, `${entity.id}.${p.name} ontology property`)
      const inputs = contract.mappings.filter(m => sources.some(s => s.id === m.source) && m.target === p.name)
      if (!inputs.length) add('unbound', `${entity.id}.${p.name}: map at least one source column.`)
      if (new Set(inputs.map(m => m.source)).size !== inputs.length) add('ambiguous', `${entity.id}.${p.name}: only one column per source can feed a property.`)
      if (p.strategy === 'source_priority') {
        const ids = inputs.map(m => m.source)
        if (p.priority.length !== ids.length || new Set(p.priority).size !== ids.length || ids.some(id => !p.priority.includes(id))) {
          add('priority', `${entity.id}.${p.name}: priority must list each contributing source exactly once.`)
        }
      } else if (p.priority.length) add('priority', `${entity.id}.${p.name}: require_equal cannot have a priority list.`)
    }
  }
  for (const s of contract.sources) {
    name(s.id, 'Source'); name(s.schema, 'Schema'); name(s.table, 'Silver table')
    unique(s.columns.map(c => c.name), `${s.id} columns`)
    if (!guid.test(s.workspaceId) || !guid.test(s.lakehouseId)) add('identity', `${s.id}: workspace and Lakehouse item GUIDs are required.`)
    const entity = contract.entities.find(e => e.id === s.entity)
    if (!entity) add('entity', `${s.id}: select an existing ontology entity.`)
    if (!s.columns.some(c => c.name === s.keyColumn && c.type === 'string')) add('key', `${s.id}: source key must be a string column.`)
    for (const c of s.columns) {
      name(c.name, 'Source column')
      const mapping = contract.mappings.find(m => m.source === s.id && m.column === c.name)
      if (!mapping) { add('new_column', `${s.id}.${c.name}: new column needs a mapping or an explicit exclusion.`); continue }
      if (!mapping.reviewed || !mapping.reason.trim()) add('review', `${s.id}.${c.name}: review and a business rationale are required.`)
      const target = entity?.properties.find(p => p.name === mapping.target)
      if (mapping.target !== null && !target) add('target', `${s.id}.${c.name}: unknown canonical property.`)
      if (target && target.type !== c.type) add('type', `${s.id}.${c.name}: source and target types differ; conform the Silver column upstream.`)
      if (mapping.transform !== 'identity' && c.type !== 'string') add('transform', `${s.id}.${c.name}: text normalization requires a string.`)
      if (c.name === s.keyColumn && (mapping.target !== entity?.key || mapping.transform !== 'identity')) add('key', `${s.id}: key must map unchanged to the canonical key.`)
      if (c.name !== s.keyColumn && mapping.target === entity?.key) add('key', `${s.id}: only the declared source key can map to the canonical key.`)
    }
  }
  for (const m of contract.mappings) {
    if (!contract.sources.some(s => s.id === m.source && s.columns.some(c => c.name === m.column))) add('removed_column', `${m.source}.${m.column}: source column was removed; resolve the stale decision.`)
  }
  const targets = new Set<string>()
  for (const r of contract.relationships) {
    name(r.name, 'Relationship')
    label(r.ontologyRelationship, `${r.name} ontology relationship`)
    const from = contract.entities.find(e => e.id === r.from)
    const to = contract.entities.find(e => e.id === r.to)
    const fk = from?.properties.find(p => p.name === r.column)
    if (!from || !to || from === to || !fk || fk.type !== 'string') add('relationship', `${r.name}: only declared string-key, many-to-one relationships between different entities are supported.`)
    if (targets.has(`${r.from}.${r.column}`)) add('relationship', `${r.from}.${r.column}: multiple active relationships are ambiguous.`)
    targets.add(`${r.from}.${r.column}`)
  }
  // Power BI active paths must be unambiguous. A forest is a deliberately conservative v1 limit.
  const components = new Map(contract.entities.map(e => [e.id, e.id]))
  const root = (id: string): string => components.get(id) === id ? id : root(components.get(id)!)
  for (const r of contract.relationships) {
    if (!components.has(r.from) || !components.has(r.to)) continue
    const a = root(r.from), b = root(r.to)
    if (a === b) add('relationship', `${r.name}: cyclic or multiple active relationship paths are not supported.`)
    else components.set(a, b)
  }
  return issues
}

export function updateMapping(contract: Contract, mapping: Mapping): Contract {
  return { ...contract, mappings: [...contract.mappings.filter(m => m.source !== mapping.source || m.column !== mapping.column), mapping] }
}

export function reconcileSources(contract: Contract, incoming: unknown): Contract {
  const envelope = object(incoming)
  if (!array(source)(envelope.sources)) throw new Error('Inventory must contain a sources array in contract format.')
  const next = envelope.sources as Source[]
  const old = contract.sources
  return {
    ...contract, sources: next,
    mappings: contract.mappings.map(m => {
      const before = old.find(s => s.id === m.source), after = next.find(s => s.id === m.source)
      const unchanged = before && after
        && JSON.stringify({ ...before, columns: [] }) === JSON.stringify({ ...after, columns: [] })
        && before.columns.find(c => c.name === m.column)?.type === after.columns.find(c => c.name === m.column)?.type
      return unchanged ? m : { ...m, reviewed: false }
    }),
  }
}

export function suggestions(contract: Contract, source: Source, columnName: string): string[] {
  const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '')
  const normalized = normalize(columnName)
  return contract.entities.find(e => e.id === source.entity)?.properties.filter(p =>
    normalize(p.name) === normalized || normalize(p.label) === normalized,
  ).map(p => p.name) ?? []
}

export function columnPeers(contract: Contract, source: Source, columnName: string): { source: string; column: string }[] {
  const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '')
  const current = contract.mappings.find(m => m.source === source.id && m.column === columnName)
  return contract.sources.filter(s => s.entity === source.entity && s.id !== source.id).flatMap(s => s.columns.filter(c => {
    const peer = contract.mappings.find(m => m.source === s.id && m.column === c.name)
    if (current?.target && current.target === peer?.target) return true
    if (current?.reviewed && peer?.reviewed) return false
    return normalize(c.name) === normalize(columnName)
  }).map(c => ({ source: s.id, column: c.name })))
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(',')}}`
}
export async function fingerprint(value: unknown): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(value)))
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function preview(contract: Contract, samples: Samples) {
  const rows: Record<string, Record<string, unknown>[]> = {}
  const conflicts: string[] = []
  const lineage: { entity: string; key: string; property: string; source: string; column: string }[] = []
  for (const entity of contract.entities) {
    const groups = new Map<string, { source: string; row: Record<string, unknown> }[]>()
    for (const s of contract.sources.filter(s => s.entity === entity.id)) {
      const seen = new Set<string>()
      if (!Object.hasOwn(samples, s.id) || !Array.isArray(samples[s.id])) { conflicts.push(`${s.id}: sample is unavailable.`); continue }
      for (const row of samples[s.id]) {
        const key = row[s.keyColumn]
        if (typeof key !== 'string' || !key.trim()) { conflicts.push(`${s.id}: missing or invalid business key.`); continue }
        if (seen.has(key)) { conflicts.push(`${s.id}: duplicate business key ${key}.`); continue }
        seen.add(key)
        groups.set(key, [...(groups.get(key) ?? []), { source: s.id, row }])
      }
    }
    rows[entity.id] = []
    if (!groups.size) conflicts.push(`${entity.id}: no sample business keys are available.`)
    for (const [key, inputs] of groups) {
      const result: Record<string, unknown> = {}
      let blocked = false
      for (const p of entity.properties) {
        const values: { source: string; column: string; value: unknown }[] = []
        for (const input of inputs) {
          const m = contract.mappings.find(m => m.source === input.source && m.target === p.name)
          if (!m) continue
          let value = input.row[m.column]
          if (value === null) continue
          if (value === undefined || (p.type === 'string' && typeof value !== 'string')
            || (p.type === 'boolean' && typeof value !== 'boolean')
            || (p.type === 'int64' && (typeof value !== 'number' || !Number.isSafeInteger(value)))) {
            conflicts.push(`${entity.id}/${key}/${p.name}: invalid or missing source value.`); blocked = true; continue
          }
          if (typeof value === 'string') {
            if (m.transform !== 'identity') value = value.trim()
            if (m.transform === 'lower') value = (value as string).toLowerCase()
            if (m.transform === 'upper') value = (value as string).toUpperCase()
            if (value === '') continue
          }
          values.push({ source: input.source, column: m.column, value })
        }
        if (!values.length && p.required) { conflicts.push(`${entity.id}/${key}/${p.name}: required value is missing.`); blocked = true }
        if (p.strategy === 'require_equal' && new Set(values.map(v => canonicalJson(v.value))).size > 1) {
          conflicts.push(`${entity.id}/${key}/${p.name}: overlapping source values disagree.`); blocked = true
        }
        const winner = p.strategy === 'source_priority'
          ? [...values].sort((a, b) => p.priority.indexOf(a.source) - p.priority.indexOf(b.source))[0]
          : [...values].sort((a, b) => a.source < b.source ? -1 : a.source > b.source ? 1 : 0)[0]
        result[p.name] = winner?.value ?? null
        if (winner) lineage.push({ entity: entity.id, key, property: p.name, source: winner.source, column: winner.column })
      }
      if (!blocked) rows[entity.id].push(result)
    }
  }
  for (const r of contract.relationships) {
    const target = contract.entities.find(e => e.id === r.to)
    if (!target) continue
    const keys = new Set((rows[r.to] ?? []).map(row => row[target.key]))
    for (const row of rows[r.from] ?? []) {
      if (row[r.column] !== null && !keys.has(row[r.column])) conflicts.push(`${r.name}: orphan foreign key ${String(row[r.column])}.`)
    }
  }
  return { rows, conflicts, lineage }
}
