export type OntologyGraphNode = {
  oid: string
  labels: string[]
  properties: Record<string, unknown>
  entityTypeId?: string
}

export type OntologyGraphEdge = {
  oid: string
  labels: string[]
  sourceOid: string
  targetOid: string
  properties: Record<string, unknown>
  relationshipTypeId?: string
}

export type OntologyGraph = {
  ontologyId?: string
  graphModelId: string
  graphModelName: string
  nodes: OntologyGraphNode[]
  edges: OntologyGraphEdge[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function decodeElement(value: unknown, context: string): OntologyGraphNode {
  if (typeof value !== 'string') throw new Error(`${context}: expected a serialized graph element.`)
  let element: unknown
  try { element = JSON.parse(value) }
  catch { throw new Error(`${context}: invalid graph JSON.`) }
  if (!isRecord(element) || typeof element.oid !== 'string' || !element.oid.trim()
    || !Array.isArray(element.labels) || !element.labels.every(label => typeof label === 'string' && label.trim())) {
    throw new Error(`${context}: invalid graph identity or labels.`)
  }
  const properties = element.properties === undefined ? {} : element.properties
  if (!isRecord(properties)) {
    throw new Error(`${context}: invalid graph properties.`)
  }
  return { oid: element.oid, labels: element.labels, properties }
}

export function parseOntologyGraph(
  graphModelId: string,
  graphModelName: string,
  nodeRows: Array<Record<string, unknown>>,
  edgeRows: Array<Record<string, unknown>>,
): OntologyGraph {
  const nodes = nodeRows.map((row, index) => decodeElement(row.node, `Graph node row ${index + 1}`))
  const nodeIds = new Set(nodes.map(node => node.oid))
  if (nodeIds.size !== nodes.length) throw new Error('Graph query returned duplicate node identities.')
  const edges = edgeRows.map((row, index) => {
    const context = `Graph relationship row ${index + 1}`
    const element = decodeElement(row.relationship, context)
    const source = decodeElement(row.source, `${context} source`)
    const target = decodeElement(row.target, `${context} target`)
    if (!nodeIds.has(source.oid) || !nodeIds.has(target.oid)) {
      throw new Error(`${context}: endpoint missing from node results. Refresh the graph; partial topology will not be displayed.`)
    }
    return { ...element, sourceOid: source.oid, targetOid: target.oid }
  })
  if (new Set(edges.map(edge => edge.oid)).size !== edges.length) throw new Error('Graph query returned duplicate relationship identities.')
  return { graphModelId, graphModelName, nodes, edges }
}