import { requireV2Generation } from './ontologyArtifactDiscovery'
import { decodeDefinitionText, type OntologyDefinition } from './ontologyContract'

type WorkspaceItem = { id: string; type: string; displayName: string }

export function selectDataAgent(items: WorkspaceItem[]): WorkspaceItem | undefined {
  const dataAgents = items.filter(item => item.type === 'DataAgent')
  const versioned = dataAgents
    .filter(item => item.displayName.startsWith('RTI_Demo_Agent_'))
    .sort((left, right) => right.displayName.localeCompare(left.displayName, undefined, { numeric: true, sensitivity: 'base' }))
  return versioned[0] ?? (dataAgents.length === 1 ? dataAgents[0] : undefined)
}

export function requireDataAgentEndpoint(endpoint: string | undefined, generation: unknown): string {
  requireV2Generation(generation)
  if (endpoint) return endpoint
  throw new Error(
    'No published Fabric Data Agent was found in this workspace. SQL/GraphQL provisioning does not confirm agent readiness. '
    + 'Ontology v2 source onboarding may be blocked. '
    + 'Check data_agent_deployment_status and data_agent_deployment_reason in the shared notebook configuration, then publish a supported Data Agent separately and refresh discovery.',
  )
}

export function verifyPublishedAgentOntology(definition: OntologyDefinition, workspaceId: string, ontologyId: string): void {
  const parts = definition.definition?.parts?.filter(part => /^Files\/Config\/published\/[^/]+\/datasource\.json$/.test(part.path)) ?? []
  let matched = false
  for (const part of parts) {
    let source: Record<string, unknown>
    try {
      const value: unknown = JSON.parse(decodeDefinitionText(part))
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
      source = value as Record<string, unknown>
    } catch {
      throw new Error(`Data Agent source verification failed: ${part.path} is not a valid published datasource definition.`)
    }
    if (typeof source.type !== 'string') throw new Error('Data Agent source verification failed: a published source has no type.')
    if (source.type.toLowerCase() !== 'ontology') continue
    if (typeof source.artifactId !== 'string' || source.artifactId.toLowerCase() !== ontologyId.toLowerCase()
      || typeof source.workspaceId !== 'string' || source.workspaceId.toLowerCase() !== workspaceId.toLowerCase()) {
      throw new Error('The published Data Agent references a different or unverified Ontology. Replace legacy sources with the selected Ontology v2 and republish before using this app.')
    }
    matched = true
  }
  if (!matched) throw new Error('Data Agent Ontology v2 source is unverified: no published datasource references the selected generation-2 item. Check data_agent_deployment_status/reason; v2 onboarding may be product-blocked. Draft sources and notebook completion are not proof of a published v2 source.')
}

export async function invokeVerifiedDataAgent<T>(
  identity: { generation: unknown; workspaceId: string; ontologyId: string },
  readDefinition: () => Promise<OntologyDefinition>,
  invokeMcp: () => Promise<T>,
): Promise<T> {
  requireV2Generation(identity.generation)
  try {
    verifyPublishedAgentOntology(await readDefinition(), identity.workspaceId, identity.ontologyId)
  } catch (error) {
    throw new Error(`Data Agent v2 source verification failed: ${error instanceof Error ? error.message : 'Unable to read the published definition.'}`, { cause: error })
  }
  return invokeMcp()
}
