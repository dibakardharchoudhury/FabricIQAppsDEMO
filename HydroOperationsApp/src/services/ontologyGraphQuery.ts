import { requireV2Generation } from './ontologyArtifactDiscovery'
import type { OntologyContract } from './ontologyContract'
import { parseOntologyGraph, type OntologyGraph } from './ontologyGraph'

export type OntologyGraphBinding = {
  workspaceId: string
  ontologyId: string
  graphModelId: string
  nodeTypes?: Record<string, string>
  edgeTypes?: Record<string, string>
}
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export const GRAPH_SETUP_REQUIRED = 'Materialize the graph from the selected Ontology v2 using Manage graph, then configure RAYFIN_PUBLIC_ONTOLOGY_GRAPH_BINDING with its verified workspaceId, ontologyId and graphModelId. Native queries do not substitute Lakehouse joins or guess graph ownership.'

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

export function parseGraphBinding(value: string | undefined, workspaceId: string, ontologyId: string): OntologyGraphBinding {
  if (!value?.trim()) throw new Error(GRAPH_SETUP_REQUIRED)
  let binding: unknown
  try { binding = JSON.parse(value) }
  catch { throw new Error(`Invalid ontology graph binding JSON. ${GRAPH_SETUP_REQUIRED}`) }
  if (!isRecord(binding) || typeof binding.workspaceId !== 'string' || !GUID.test(binding.workspaceId)
    || typeof binding.ontologyId !== 'string' || !GUID.test(binding.ontologyId)
    || typeof binding.graphModelId !== 'string' || !GUID.test(binding.graphModelId)) {
    throw new Error(`Ontology graph binding requires three valid GUIDs. ${GRAPH_SETUP_REQUIRED}`)
  }
  if (binding.workspaceId.toLowerCase() !== workspaceId.toLowerCase() || binding.ontologyId.toLowerCase() !== ontologyId.toLowerCase()) {
    throw new Error('The configured graph binding belongs to a different workspace or ontology. Verify the selected v2 ontology and its materialized graph before updating the binding.')
  }
  const mappings = (key: 'nodeTypes' | 'edgeTypes'): Record<string, string> | undefined => {
    const value = binding[key]
    if (value === undefined) return undefined
    if (!isRecord(value) || !Object.values(value).every(target => typeof target === 'string' && target.trim())) {
      throw new Error(`Graph binding ${key} must map queryable graph aliases to exact ontology names.`)
    }
    return Object.fromEntries(Object.entries(value).map(([alias, target]) => [alias, String(target)]))
  }
  return {
    workspaceId: binding.workspaceId, ontologyId: binding.ontologyId, graphModelId: binding.graphModelId,
    nodeTypes: mappings('nodeTypes'), edgeTypes: mappings('edgeTypes'),
  }
}

type GraphReader = (url: string, init?: RequestInit) => Promise<Response>

async function jsonResponse(response: Response, operation: string): Promise<unknown> {
  if (response.status === 204) throw new Error(`${operation} is not ready (204 No Content). Materialize and refresh the selected ontology's graph in Fabric before querying.`)
  if (!response.ok) throw new Error(`${operation} failed (${response.status}). Verify Fabric permissions and graph materialization/refresh readiness.`)
  try { return await response.json() as unknown }
  catch { throw new Error(`${operation} returned invalid JSON.`) }
}

export async function executeGraphRows(
  endpoint: string,
  query: string,
  request: GraphReader,
  maxRows: number,
): Promise<Array<Record<string, unknown>>> {
  const rows: Array<Record<string, unknown>> = []
  const tokens = new Set<string>()
  const deadline = Date.now() + 120_000
  let continuation: string | undefined
  for (let page = 0; page < 120; page++) {
    if (Date.now() >= deadline) throw new Error('Native ontology graph query timed out. Retry after graph refresh completes.')
    const url = new URL(endpoint)
    if (continuation) url.searchParams.set('continuationToken', continuation)
    const payload = await jsonResponse(await request(url.href, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ query }),
      signal: AbortSignal.timeout(Math.max(1, deadline - Date.now())),
    }), 'Native ontology graph query')
    if (!isRecord(payload) || !isRecord(payload.status) || typeof payload.status.code !== 'string') {
      throw new Error('Graph query returned no valid execution status.')
    }
    if (!['00000', '02000'].includes(payload.status.code)) {
      throw new Error(`Graph query ${payload.status.code}: ${String(payload.status.description ?? 'execution failed or returned a warning; incomplete results will not be displayed')}`)
    }
    for (const additional of [payload.additionalStatuses, payload.status.additionalStatuses]) {
      if (additional !== undefined && (!Array.isArray(additional) || additional.some(status =>
        !isRecord(status) || !['00000', '02000'].includes(String(status.code))))) {
        throw new Error('Graph query returned additional warnings/errors, possibly truncated results. Narrow the ontology projection; incomplete topology will not be displayed.')
      }
    }
    if (!isRecord(payload.result) || payload.result.kind !== 'TABLE' || !Array.isArray(payload.result.data)
      || !payload.result.data.every(isRecord)) {
      throw new Error('Graph query did not return a valid TABLE result. Incomplete topology will not be displayed.')
    }
    rows.push(...payload.result.data)
    if (rows.length > maxRows) throw new Error(`Ontology graph exceeds the ${maxRows}-row visualization limit. Use a smaller ontology graph projection; partial topology will not be displayed.`)
    const next: unknown = payload.result.nextPage
    if (next === undefined || next === null) return rows
    if (typeof next !== 'string' || !next || (tokens.has(next) && payload.result.data.length > 0)) throw new Error('Graph query returned an invalid or repeated continuation token.')
    tokens.add(next)
    continuation = next
    if (!payload.result.data.length) await new Promise(resolve => setTimeout(resolve, 1000))
  }
  throw new Error('Ontology graph query exceeded its continuation limit. Retry after graph refresh completes.')
}

export function resolveGraphSchema(schema: unknown, ontology: OntologyContract, binding: OntologyGraphBinding) {
  if (!isRecord(schema) || !Array.isArray(schema.nodeTypes) || !Array.isArray(schema.edgeTypes) || !schema.nodeTypes.length) {
    throw new Error('The configured ontology graph has no queryable node schema. Materialize and refresh it in Fabric before querying.')
  }
  const readType = (value: unknown) => {
    if (!isRecord(value) || typeof value.alias !== 'string' || !value.alias
      || !Array.isArray(value.labels) || !value.labels.length || !value.labels.every(label => typeof label === 'string' && label)) {
      throw new Error('Queryable graph schema contains an invalid alias or label set.')
    }
    return { alias: value.alias, labels: value.labels as string[], value }
  }
  const resolve = <T extends { id: string; name: string }>(alias: string, labels: string[], mapping: Record<string, string> | undefined, types: T[], kind: string) => {
    const configured = mapping?.[alias]
    const candidates = types.filter(type => configured !== undefined
      ? type.name === configured || type.id === configured
      : labels.includes(type.name) || labels.includes(type.id))
    if (candidates.length !== 1) {
      throw new Error(`Graph ${kind} alias "${alias}" cannot be matched unambiguously to the live ontology. Configure its exact ${kind}Types alias mapping; namespace separators are not inferred.`)
    }
    return candidates[0]
  }
  const nodes = schema.nodeTypes.map(value => {
    const type = readType(value)
    return { ...type, entity: resolve(type.alias, type.labels, binding.nodeTypes, ontology.entityTypes, 'node') }
  })
  const aliases = new Map(nodes.map(type => [type.alias, type.entity]))
  if (aliases.size !== nodes.length) throw new Error('Queryable graph schema contains duplicate node aliases.')
  const edges = schema.edgeTypes.map(value => {
    const type = readType(value)
    const relationship = resolve(type.alias, type.labels, binding.edgeTypes, ontology.relationshipTypes, 'edge')
    const source = type.value.sourceNodeType
    const target = type.value.destinationNodeType
    if (!isRecord(source) || typeof source.alias !== 'string' || !isRecord(target) || typeof target.alias !== 'string'
      || aliases.get(source.alias)?.id !== relationship.sourceEntityTypeId
      || aliases.get(target.alias)?.id !== relationship.targetEntityTypeId) {
      throw new Error(`Graph relationship "${type.alias}" endpoints disagree with the ontology definition.`)
    }
    return { ...type, relationship }
  })
  if (new Set(edges.map(type => type.alias)).size !== edges.length) throw new Error('Queryable graph schema contains duplicate edge aliases.')
  for (const [mapping, types] of [[binding.nodeTypes, nodes], [binding.edgeTypes, edges]] as const) {
    if (Object.keys(mapping ?? {}).some(alias => !types.some(type => type.alias === alias))) {
      throw new Error('Graph binding contains an alias absent from the current queryable graph schema. Refresh the mapping after projection changes.')
    }
  }
  return { nodes, edges }
}

export async function queryBoundOntologyGraph(
  binding: OntologyGraphBinding,
  ontology: OntologyContract,
  request: GraphReader,
): Promise<OntologyGraph> {
  requireV2Generation(ontology.generation)
  if (binding.ontologyId.toLowerCase() !== ontology.id.toLowerCase()) throw new Error('Graph binding does not match the live ontology contract.')
  const base = `https://api.fabric.microsoft.com/v1/workspaces/${encodeURIComponent(binding.workspaceId)}/graphModels/${encodeURIComponent(binding.graphModelId)}`
  const metadata = await jsonResponse(await request(base), 'GraphModel metadata')
  if (!isRecord(metadata) || typeof metadata.id !== 'string' || metadata.id.toLowerCase() !== binding.graphModelId.toLowerCase()
    || metadata.type !== 'GraphModel' || typeof metadata.displayName !== 'string') {
    throw new Error('GraphModel metadata does not match the explicitly configured graph identity.')
  }
  const schema = resolveGraphSchema(await jsonResponse(await request(`${base}/getQueryableGraphType?beta=true`), 'Queryable graph schema'), ontology, binding)
  const endpoint = `${base}/executeQuery?beta=true`
  const [nodes, edges] = await Promise.all([
    executeGraphRows(endpoint, 'MATCH (n) RETURN TO_JSON_STRING(n) AS `node` LIMIT 2001', request, 2000),
    executeGraphRows(endpoint, 'MATCH (source)-[`relationship`]->(target) RETURN TO_JSON_STRING(source) AS `source`, TO_JSON_STRING(`relationship`) AS `relationship`, TO_JSON_STRING(target) AS `target` LIMIT 4001', request, 4000),
  ])
  const graph = parseOntologyGraph(binding.graphModelId, metadata.displayName, nodes, edges)
  for (const node of graph.nodes) {
    const types = schema.nodes.filter(type => type.labels.every(label => node.labels.includes(label)))
    if (types.length !== 1) throw new Error(`Graph node "${node.oid}" has no unambiguous queryable ontology type.`)
    node.entityTypeId = types[0].entity.id
  }
  const nodesById = new Map(graph.nodes.map(node => [node.oid, node]))
  for (const edge of graph.edges) {
    const types = schema.edges.filter(type => type.labels.every(label => edge.labels.includes(label)))
    if (types.length !== 1) throw new Error(`Graph edge "${edge.oid}" has no unambiguous queryable ontology relationship.`)
    const relationship = types[0].relationship
    if (nodesById.get(edge.sourceOid)?.entityTypeId !== relationship.sourceEntityTypeId
      || nodesById.get(edge.targetOid)?.entityTypeId !== relationship.targetEntityTypeId) {
      throw new Error(`Graph edge "${edge.oid}" endpoints disagree with the live ontology.`)
    }
    edge.relationshipTypeId = relationship.id
  }
  return { ...graph, ontologyId: ontology.id }
}
