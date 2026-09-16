export type SemanticArtifact = { id: string; type: string; displayName: string }

export function selectOntology(items: SemanticArtifact[], configuredName?: string): SemanticArtifact | undefined {
  const ontologies = items.filter(item => item.type === 'Ontology')
  if (configuredName) {
    const configured = ontologies.find(item => item.displayName === configuredName)
    if (configured) return configured
  }
  return ontologies.length === 1 ? ontologies[0] : undefined
}

export function selectGraphModel(
  items: SemanticArtifact[],
  ontologyEntityNames: string[],
  labelsByGraphModelId: ReadonlyMap<string, ReadonlySet<string>>,
): SemanticArtifact | undefined {
  const graphModels = items.filter(item => item.type === 'GraphModel')
  if (graphModels.length === 1) return graphModels[0]

  const entityNames = new Set(ontologyEntityNames.filter(Boolean))
  const scored = graphModels.map(item => ({
    item,
    score: [...(labelsByGraphModelId.get(item.id) ?? [])].filter(label => entityNames.has(label)).length,
  })).sort((left, right) => right.score - left.score)

  return scored[0]?.score > 0 && scored[0].score > (scored[1]?.score ?? 0) ? scored[0].item : undefined
}