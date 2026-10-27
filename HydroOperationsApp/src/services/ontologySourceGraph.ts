import type { OntologyContract, OntologySourceBinding } from './ontologyContract'
import type { OntologyGraph, OntologyGraphEdge, OntologyGraphNode } from './ontologyGraph'
import type { LakehousePage } from './lakehouseConnector'

export const SOURCE_GRAPH_LIMITS = { nodes: 2_000, edges: 4_000, pages: 20, bytes: 8 * 1024 * 1024, timeoutMs: 20_000 } as const
export type SourceGraphCoverage = { id: string; name: string; included: boolean; reason?: string; count?: number }
export type OntologySourceGraph = {
  execution: 'source-backed'
  ontologyId: string
  nodes: OntologyGraphNode[]
  edges: OntologyGraphEdge[]
  evidence: {
    contractSignature: string
    startedAt: string
    completedAt: string
    elapsedMs: number
    consistency: 'non-atomic live reads'
    entities: SourceGraphCoverage[]
    relationships: SourceGraphCoverage[]
    sources: Array<{ entityTypeId: string; workspaceId: string; itemId: string; schema: string; table: string; observedAt: string }>
    warnings: string[]
  }
}
export type OntologyInstanceGraph = OntologyGraph | OntologySourceGraph
export type OntologySourceReader = {
  workspaceId: string
  itemId: string
  itemKind: 'Lakehouse' | 'SQLDatabase'
  schema: string
  table: string
  columns: readonly string[]
  read: (cursor?: string) => Promise<LakehousePage<Record<string, unknown>>>
}

export async function ontologyContractSignature(contract: OntologyContract): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(contract)))
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
}

function scalarKey(value: unknown, context: string): string {
  if ((typeof value === 'string' && value.trim()) || typeof value === 'boolean'
    || (typeof value === 'number' && Number.isFinite(value))) return JSON.stringify([typeof value, value])
  throw new Error(`${context}: identity/join value must be a nonempty scalar. Partial topology is not displayed.`)
}

export async function readOntologySourceGraph(
  contract: OntologyContract, readers: OntologySourceReader[],
): Promise<OntologySourceGraph> {
  if (contract.generation !== 2 || !contract.sourceBindings) throw new Error('Live graph requires verified generation-2 source bindings.')
  const started = Date.now()
  const graph: OntologySourceGraph = {
    execution: 'source-backed', ontologyId: contract.id, nodes: [], edges: [],
    evidence: {
      contractSignature: await ontologyContractSignature(contract),
      startedAt: new Date(started).toISOString(), completedAt: '', elapsedMs: 0,
      consistency: 'non-atomic live reads', entities: [], relationships: [], sources: [],
      warnings: [...(contract.warnings ?? [])],
    },
  }
  const plans = new Map<string, { binding: OntologySourceBinding; reader: OntologySourceReader }>()
  const entities = new Map(contract.entityTypes.map(entity => [entity.id, entity]))
  const sourceProjection = new Map(contract.sourceBindings.map(projection => [projection.entityTypeId, projection]))
  for (const entity of contract.entityTypes) {
    const projection = sourceProjection.get(entity.id)
    const binding = projection?.binding
    const omit = (reason: string) => graph.evidence.entities.push({ id: entity.id, name: entity.name, included: false, reason })
    if (!binding) { omit(projection?.unsupportedReason ?? 'No verified physical source binding.'); continue }
    const sameItem = readers.filter(reader => reader.workspaceId.toLowerCase() === binding.workspaceId && reader.itemId.toLowerCase() === binding.itemId)
    if (!sameItem.length) throw new Error(`Ontology entity ${entity.name} points outside the configured connector source identities. Redeploy for the correct target; no source reads were started.`)
    if (binding.itemKind && sameItem.some(reader => reader.itemKind !== binding.itemKind)) throw new Error(`Ontology entity ${entity.name}: source kind conflicts with the connector.`)
    const matches = sameItem.filter(reader => reader.table === binding.table
      && (binding.schema ? reader.schema === binding.schema : reader.itemKind === 'Lakehouse' && reader.schema === 'dbo'))
    if (matches.length > 1) throw new Error(`Ontology entity ${entity.name}: ambiguous connector binding.`)
    const reader = matches[0]
    if (!reader) { omit('Physical table/schema is not exposed by a verified read connector.'); continue }
    const missing = Object.entries(binding.properties).filter(([, column]) => !reader.columns.includes(column)).map(([property]) => property)
    if (missing.length || binding.omittedProperties.includes(binding.keyProperty)) {
      omit(`Connector does not expose the complete scalar projection (${missing.join(', ') || 'identity'}).`)
      continue
    }
    if (binding.omittedProperties.length) graph.evidence.warnings.push(`${entity.name}: properties outside the primary scalar source are not evaluated: ${binding.omittedProperties.join(', ')}.`)
    plans.set(entity.id, { binding, reader })
    graph.evidence.entities.push({ id: entity.id, name: entity.name, included: true })
  }
  if (!plans.size) throw new Error('No ontology entities have a supported, verified connector projection. See ontology bindings and connector coverage.')
  const relationships = contract.relationshipTypes.filter(relationship => {
    const from = plans.get(relationship.sourceEntityTypeId)
    const to = plans.get(relationship.targetEntityTypeId)
    const included = Boolean(from && to && !relationship.compatibilityUnsupported && relationship.sourceKeys.length === 1 && relationship.targetKeys.length === 1)
    graph.evidence.relationships.push({
      id: relationship.id, name: relationship.name, included,
      ...(!included ? { reason: !from || !to ? 'An endpoint is outside the supported entity projection.' : 'Requires a declared single-column physical relationship.' } : {}),
    })
    if (included && from && to) {
      for (const [plan, keys] of [[from, relationship.sourceKeys], [to, relationship.targetKeys]] as const) {
        const column = plan.binding.columns[keys[0]]
        if (!column || !plan.reader.columns.includes(column)) throw new Error(`Ontology relationship ${relationship.name}: bound join column is not exposed by its connector.`)
      }
    }
    return included
  })
  let bytes = 0
  const rows = new Map<string, Array<{ row: Record<string, unknown>; node: OntologyGraphNode }>>()
  let failure: Error | undefined
  const check = () => {
    if (failure) throw failure
    if (Date.now() - started >= SOURCE_GRAPH_LIMITS.timeoutMs) throw new Error('Live ontology source reads exceeded 20 seconds. No partial graph is displayed.')
  }
  const queue = [...plans.entries()]
  const worker = async () => {
    while (queue.length) {
      check()
      const [entityId, { binding, reader }] = queue.shift()!
      const entity = entities.get(entityId)!
      const records: Array<{ row: Record<string, unknown>; node: OntologyGraphNode }> = []
      rows.set(entityId, records)
      const identities = new Set<string>()
      const cursors = new Set<string>()
      let cursor: string | undefined
      for (let pageNumber = 0; ; pageNumber++) {
        check()
        if (pageNumber >= SOURCE_GRAPH_LIMITS.pages) throw new Error(`${entity.name}: connector page budget exceeded; partial data is not accepted.`)
        const page = await reader.read(cursor)
        check()
        if (!Array.isArray(page.items) || typeof page.hasNextPage !== 'boolean') throw new Error(`${entity.name}: invalid connector page.`)
        for (const row of page.items) {
          if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error(`${entity.name}: invalid source row.`)
          const key = scalarKey(row[binding.keyColumn], entity.name)
          if (identities.has(key)) throw new Error(`${entity.name}: duplicate entity identity in the source; partial data is not accepted.`)
          identities.add(key)
          const properties = Object.fromEntries(Object.entries(binding.properties).map(([property, column]) => {
            const value = row[column]
            if (!Object.hasOwn(row, column)) throw new Error(`${entity.name}: connector omitted a declared scalar column.`)
            if ((typeof value === 'number' && !Number.isFinite(value))
              || (value !== null && value !== undefined && !(value instanceof Date) && !['string', 'number', 'boolean'].includes(typeof value))) throw new Error(`${entity.name}: connector returned an invalid scalar property.`)
            return [property, value instanceof Date ? value.toISOString() : value]
          }))
          const node = { oid: JSON.stringify(['source', entityId, key]), labels: [entity.name], entityTypeId: entityId, properties }
          bytes += new TextEncoder().encode(JSON.stringify(node)).length
          if (graph.nodes.length >= SOURCE_GRAPH_LIMITS.nodes || bytes > SOURCE_GRAPH_LIMITS.bytes) throw new Error('Live ontology graph exceeds the bounded node/payload budget. No truncated graph is displayed.')
          graph.nodes.push(node)
          records.push({ row, node })
        }
        if (!page.hasNextPage) break
        if (!page.endCursor || cursors.has(page.endCursor)) throw new Error(`${entity.name}: connector pagination did not advance.`)
        cursors.add(page.endCursor)
        cursor = page.endCursor
      }
      graph.evidence.entities.find(item => item.id === entityId)!.count = records.length
      graph.evidence.sources.push({
        entityTypeId: entityId, workspaceId: binding.workspaceId, itemId: binding.itemId,
        schema: reader.schema, table: reader.table, observedAt: new Date().toISOString(),
      })
    }
  }
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    await Promise.race([
      Promise.all(Array.from({ length: Math.min(4, plans.size) }, () => worker().catch(error => {
        failure = error instanceof Error ? error : new Error('Live ontology connector read failed.')
        throw failure
      }))),
      new Promise<never>((_, reject) => { timer = setTimeout(() => {
        failure = new Error('Live ontology source reads exceeded 20 seconds. No partial graph is displayed.')
        reject(failure)
      }, SOURCE_GRAPH_LIMITS.timeoutMs) }),
    ])
  } finally { if (timer !== undefined) clearTimeout(timer) }
  for (const relationship of relationships) {
    check()
    const from = plans.get(relationship.sourceEntityTypeId)!
    const to = plans.get(relationship.targetEntityTypeId)!
    const fromColumn = from.binding.columns[relationship.sourceKeys[0]]
    const toColumn = to.binding.columns[relationship.targetKeys[0]]
    const targets = new Map<string, OntologyGraphNode[]>()
    for (const { row, node } of rows.get(relationship.targetEntityTypeId)!) {
      const value = row[toColumn]
      if (value === null || value === undefined || value === '') continue
      const key = scalarKey(value, relationship.name)
      const matches = targets.get(key) ?? []
      if (relationship.targetCardinality === 'one' && matches.length) throw new Error(`Ontology relationship ${relationship.name}: target cardinality is not one.`)
      matches.push(node)
      targets.set(key, matches)
    }
    let count = 0
    const sourceJoinKeys = new Set<string>()
    for (const { row, node } of rows.get(relationship.sourceEntityTypeId)!) {
      const value = row[fromColumn]
      if (value === null || value === undefined || value === '') continue
      const joinKey = scalarKey(value, relationship.name)
      if (relationship.sourceCardinality === 'one' && sourceJoinKeys.has(joinKey)) throw new Error(`Ontology relationship ${relationship.name}: source cardinality is not one.`)
      sourceJoinKeys.add(joinKey)
      const matches = targets.get(joinKey)
      if (!matches?.length) throw new Error(`Ontology relationship ${relationship.name}: unresolved source reference. Check data integrity, row access and cross-source freshness; incomplete topology is not displayed.`)
      for (const target of matches) {
        if (graph.edges.length >= SOURCE_GRAPH_LIMITS.edges) throw new Error('Live ontology graph exceeds its relationship budget; no partial topology is displayed.')
        const edge = {
          oid: JSON.stringify(['source', relationship.id, node.oid, target.oid]), labels: [relationship.name],
          relationshipTypeId: relationship.id, sourceOid: node.oid, targetOid: target.oid, properties: {},
        }
        bytes += new TextEncoder().encode(JSON.stringify(edge)).length
        if (bytes > SOURCE_GRAPH_LIMITS.bytes) throw new Error('Live ontology graph exceeds its payload budget; no partial topology is displayed.')
        graph.edges.push(edge)
        count++
      }
    }
    graph.evidence.relationships.find(item => item.id === relationship.id)!.count = count
  }
  if (!graph.nodes.length) throw new Error('Verified ontology sources returned no entities for this signed-in user.')
  graph.evidence.completedAt = new Date().toISOString()
  graph.evidence.elapsedMs = Date.now() - started
  return graph
}
