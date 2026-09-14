export type OntologyGraphNode = {
  oid: string
  labels: string[]
  properties: Record<string, unknown>
}

export type OntologyGraphEdge = {
  oid: string
  labels: string[]
  sourceOid: string
  targetOid: string
  properties: Record<string, unknown>
}

export type OntologyGraph = {
  graphModelId: string
  graphModelName: string
  nodes: OntologyGraphNode[]
  edges: OntologyGraphEdge[]
}

type SerializedGraphElement = {
  oid?: unknown
  labels?: unknown
  properties?: unknown
  ends?: Array<{ oid?: unknown }>
}

function decodeElement(value: unknown): SerializedGraphElement | undefined {
  if (typeof value !== 'string') return undefined
  try { return JSON.parse(value) as SerializedGraphElement }
  catch { return undefined }
}

export function parseOntologyGraph(
  graphModelId: string,
  graphModelName: string,
  nodeRows: Array<Record<string, unknown>>,
  edgeRows: Array<Record<string, unknown>>,
): OntologyGraph {
  const nodes = nodeRows.flatMap(row => {
    const element = decodeElement(row.node)
    if (!element || typeof element.oid !== 'string') return []
    return [{
      oid: element.oid,
      labels: Array.isArray(element.labels) ? element.labels.map(String) : [],
      properties: element.properties && typeof element.properties === 'object' ? element.properties as Record<string, unknown> : {},
    }]
  })
  const edges = edgeRows.flatMap(row => {
    const element = decodeElement(row.relationship)
    const source = decodeElement(row.source)
    const target = decodeElement(row.target)
    const sourceOid = typeof source?.oid === 'string' ? source.oid : element?.ends?.[0]?.oid
    const targetOid = typeof target?.oid === 'string' ? target.oid : element?.ends?.[1]?.oid
    if (!element || typeof element.oid !== 'string' || typeof sourceOid !== 'string' || typeof targetOid !== 'string') return []
    return [{
      oid: element.oid,
      labels: Array.isArray(element.labels) ? element.labels.map(String) : [],
      sourceOid,
      targetOid,
      properties: element.properties && typeof element.properties === 'object' ? element.properties as Record<string, unknown> : {},
    }]
  })
  return { graphModelId, graphModelName, nodes, edges }
}