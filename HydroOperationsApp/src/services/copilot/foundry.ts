import type { AgentAnswer } from '../assistantStream.ts'
import type { Asset3DModelRecord } from '../rayfin.ts'
import type { AgentStep } from '../agentSteps.ts'
import type { OrchestrationEvent, WorkOrderProposal } from './orchestration.ts'
import { loadCopilotSettings } from './settings.ts'
import { HostedTransport, type HostedSource } from './hostedTransport.ts'

export type { AgentStep, AgentStepStatus } from '../agentSteps.ts'
export type FoundryAnswer = AgentAnswer & {
  models?: Asset3DModelRecord[]
  orchestrationEvents?: OrchestrationEvent[]
  proposals?: WorkOrderProposal[]
  backendOwned?: boolean
}

const env = (import.meta as { env?: Record<string, string | undefined> }).env ?? {}
const invocationUrl = env.VITE_RAYFIN_FOUNDRY_INVOCATIONS_URL
const clients = new Map<'foundry' | 'data-agent', { key: string; client: HostedTransport }>()

export function isHostedFoundryConfigured() { return Boolean(invocationUrl) }
export function isFoundryConfigured() {
  return isHostedFoundryConfigured() && Boolean(loadCopilotSettings().projectEndpoint)
}

export function requireProjectEndpoint(endpoint: string): string {
  const url = new URL(endpoint)
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.services.ai.azure.com')
    || !/^\/api\/projects\/[A-Za-z0-9._-]+\/?$/.test(url.pathname) || url.search || url.hash || url.username || url.password) {
    throw new Error('Use the HTTPS Foundry project endpoint, not a model inference URL.')
  }
  return url.href.replace(/\/$/, '')
}

export function resetFoundryConversation() {
  for (const { client } of clients.values()) client.reset()
}

function hostedClient(engine: 'foundry' | 'data-agent') {
  if (!invocationUrl) throw new Error('No accepted hosted invocation endpoint is configured.')
  const project = requireProjectEndpoint(loadCopilotSettings().projectEndpoint)
  const tenant = env.VITE_FABRIC_TENANT_ID ?? env.VITE_RAYFIN_TENANT_ID
  const workspace = env.VITE_FABRIC_WORKSPACE_ID ?? env.VITE_RAYFIN_WORKSPACE_ID
  const digest = env.VITE_RAYFIN_ORCHESTRATOR_SOURCE_DIGEST
  if (!tenant || !workspace || !digest) throw new Error('Verified hosted source identity/digest configuration is missing.')
  const binding: unknown = JSON.parse(env.VITE_RAYFIN_ONTOLOGY_GRAPH_BINDING ?? 'null')
  if (!binding || typeof binding !== 'object' || !('ontologyId' in binding) || typeof binding.ontologyId !== 'string'
    || !('workspaceId' in binding) || binding.workspaceId !== workspace) {
    throw new Error('The hosted source requires the explicit selected ontology binding in the same workspace.')
  }
  const source: HostedSource = { tenant_id: tenant, workspace_id: workspace,
    ontology_id: binding.ontologyId, generation: 2, configuration_digest: digest }
  const key = JSON.stringify([project, invocationUrl, source])
  let cached = clients.get(engine)
  if (!cached || cached.key !== key) {
    cached?.client.reset()
    cached = { key, client: new HostedTransport(project, invocationUrl, source,
      async () => {
        if (requireProjectEndpoint(loadCopilotSettings().projectEndpoint) !== project) {
          throw new Error('The selected Foundry project changed. Request fresh evidence before approving work.')
        }
        return (await import('../fabric.ts')).hostedSourceTokens()
      }) }
    clients.set(engine, cached)
  }
  return cached.client
}

export async function askHostedCopilot(
  engine: 'foundry' | 'data-agent', question: string, onProgress?: (text: string) => void,
  onEvents?: (events: OrchestrationEvent[]) => void,
): Promise<FoundryAnswer> {
  const answer = await hostedClient(engine).run({
    question, ...(engine === 'data-agent' ? { native_sources: ['data-agent'] } : {}),
  }, onEvents)
  onProgress?.(answer.text)
  return { text: answer.text, visualizations: answer.visualizations,
    proposals: answer.proposals, orchestrationEvents: answer.executionEvents, backendOwned: true }
}

export async function askFoundryCopilot(
  question: string, onProgress?: (text: string) => void, onSteps?: (steps: AgentStep[]) => void,
  onEvents?: (events: OrchestrationEvent[]) => void,
): Promise<FoundryAnswer> {
  void onSteps
  return askHostedCopilot('foundry', question, onProgress, onEvents)
}
