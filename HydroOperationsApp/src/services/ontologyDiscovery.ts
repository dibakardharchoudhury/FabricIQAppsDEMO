import { requireV2Generation, selectOntology, type SemanticArtifact } from './ontologyArtifactDiscovery'
import { parseOntologyContract, type OntologyDefinition } from './ontologyContract'

export type OntologyDiscovery = {
  ontologyId?: string
  ontologyName?: string
  ontologyGeneration?: 2
  ontologyError?: string
}

type Reader = {
  metadata: (id: string) => Promise<{ properties?: { generation?: number } }>
  definition: (id: string) => Promise<OntologyDefinition>
}

/** Semantic failures must not suppress discovery of unrelated telemetry/GraphQL endpoints. */
export async function discoverOntology(
  items: SemanticArtifact[],
  configuredName: string | undefined,
  reader: Reader,
): Promise<OntologyDiscovery> {
  const ontology = selectOntology(items, configuredName)
  if (!ontology) return configuredName || items.some(item => item.type === 'Ontology')
    ? { ontologyError: 'Ontology discovery is ambiguous or the configured name was not found. Configure an exact VITE_RAYFIN_ONTOLOGY_NAME or keep one Ontology in the app workspace.' }
    : { ontologyError: 'No Ontology v2 was found. Publish a generation-2 TMDL Ontology and refresh discovery.' }
  const identity = { ontologyId: ontology.id, ontologyName: ontology.displayName }
  try {
    const item = await reader.metadata(ontology.id)
    const reported = item.properties?.generation
    requireV2Generation(reported)
    parseOntologyContract(ontology.id, ontology.displayName, await reader.definition(ontology.id), reported)
    return { ...identity, ontologyGeneration: 2 }
  } catch (error) {
    return { ...identity, ontologyError: error instanceof Error ? error.message : 'Ontology discovery failed. Refresh and check Fabric item read access.' }
  }
}
