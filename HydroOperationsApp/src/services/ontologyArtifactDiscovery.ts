export type SemanticArtifact = { id: string; type: string; displayName: string }

export function selectOntology(items: SemanticArtifact[], configuredName?: string): SemanticArtifact | undefined {
  const ontologies = items.filter(item => item.type === 'Ontology')
  if (configuredName) {
    return ontologies.find(item => item.displayName === configuredName)
  }
  return ontologies.length === 1 ? ontologies[0] : undefined
}

export const V2_REPLACEMENT_REQUIRED = 'This app requires Ontology v2. Replace the existing v1 Ontology with a generation-2 TMDL Ontology, update dependent agents and refresh discovery. Legacy Ontologies and their Graph Models will not be reused.'

export function requireV2Generation(generation: unknown): asserts generation is 2 {
  if (generation === 1) throw new Error(V2_REPLACEMENT_REQUIRED)
  if (generation !== 2) throw new Error('Ontology v2 generation could not be verified. The selected item must report numeric properties.generation = 2. Check Fabric item read access and refresh discovery.')
}
