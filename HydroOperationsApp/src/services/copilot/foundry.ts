import type { AgentAnswer, AgentVisualization } from '../assistantStream.ts'
import { foundryAgentToken, verifyDataAgentForFoundry, verifyOntologyForFoundry } from '../fabric.ts'
import type { Asset3DModelRecord } from '../rayfin.ts'
import { catalogPrompt } from './catalog.ts'
import { readResponsesStream } from './chatStream.ts'
import type { AgentStep } from '../agentSteps'
import { loadCopilotSettings, renderSystemPrompt } from './settings.ts'
import { buildToolDefinitions, createToolRuntime, describeToolCall, type ToolArguments } from './tools.ts'
import { AGENT_NAMES, buildAgentInput, DIRECT_TOOLS, parseDelegation } from './agentDefinitions.ts'
import { ANSWER_PRESENTATION_CONTRACT } from './answerPresentation.ts'
import { captureApplicationEvent, captureFoundryEvent } from './agentTrace.ts'
import { createOrchestrationEvent, type AgentRole, type OrchestrationEvent, type WorkOrderProposal } from './orchestration.ts'
import { workOrderApprovals } from './workOrderApproval.ts'

export type { AgentStep, AgentStepStatus } from '../agentSteps'
export type FoundryAnswer = AgentAnswer & {
  models?: Asset3DModelRecord[]
  orchestrationEvents?: OrchestrationEvent[]
  proposals?: WorkOrderProposal[]
}

let history: Array<{ role: 'user' | 'assistant'; content: string }> = []
let busy = false

export function isFoundryConfigured() {
  return Boolean(loadCopilotSettings().projectEndpoint)
}

export function resetFoundryConversation() {
  if (busy) throw new Error('Wait for the current Foundry request before resetting the conversation.')
  history = []
  workOrderApprovals.clear()
}

export function requireProjectEndpoint(endpoint: string): string {
  const url = new URL(endpoint)
  if (url.protocol !== 'https:' || !url.hostname.endsWith('.services.ai.azure.com')
    || !/^\/api\/projects\/[A-Za-z0-9._-]+\/?$/.test(url.pathname) || url.search || url.hash || url.username || url.password) {
    throw new Error('Use the HTTPS Foundry project endpoint, not a model inference URL.')
  }
  return url.href.replace(/\/$/, '')
}

export async function askFoundryCopilot(
  question: string,
  onProgress?: (text: string) => void,
  onSteps?: (steps: AgentStep[]) => void,
  onEvents?: (events: OrchestrationEvent[]) => void,
): Promise<FoundryAnswer> {
  if (busy) throw new Error('A Foundry request is already running. Wait for it to finish.')
  const settings = loadCopilotSettings()
  if (!settings.projectEndpoint) throw new Error('Configure the Foundry project endpoint and provision the Hydro agents before asking a question.')
  const endpoint = requireProjectEndpoint(settings.projectEndpoint)
  busy = true
  const events: OrchestrationEvent[] = []
  const steps: AgentStep[] = []
  const proposals: WorkOrderProposal[] = []
  const visualizations: AgentVisualization[] = []
  const models: Asset3DModelRecord[] = []
  let usage: FoundryAnswer['usage']
  const publish = () => onEvents?.(events.map(event => ({ ...event })))
  const publishSteps = () => onSteps?.(steps.map(step => ({ ...step })))
  const runTool = createToolRuntime(settings, { onWorkOrderProposal: proposal => proposals.push(proposal) })
  const delegated = new Set<string>()
  try {
    const token = await foundryAgentToken(true)
    if (!token) throw new Error('Foundry Agent Service sign-in is required.')
    const invoke = async (role: AgentRole, prompt: string, parentId?: string): Promise<string> => {
      const event: OrchestrationEvent = { ...createOrchestrationEvent(role, 'queued', 'Awaiting Foundry execution.'), agentName: AGENT_NAMES[role], parentId }
      events.push(event)
      publish()
      const definitions = buildToolDefinitions(settings).filter(tool => role === 'work-order' || tool.function.name !== 'propose_work_order')
      const context = role === 'supervisor' || role === 'fabric-iq'
          ? `${ANSWER_PRESENTATION_CONTRACT}\nCurrent time: ${new Date().toISOString()}`
          : `${renderSystemPrompt(settings, catalogPrompt(settings))}\n\nPermitted direct tool schemas:\n${JSON.stringify(definitions)}\nUse hydro_query to execute these schemas. Never call a write operation. Work-order approval is exclusively handled by the human review card.`
      const input: unknown[] = buildAgentInput(context, history, prompt)
      try {
        if (role === 'fabric-iq') await Promise.all([verifyDataAgentForFoundry(), verifyOntologyForFoundry()])
        for (let round = 0; round < 6; round++) {
          const response = await fetch(`${endpoint}/openai/v1/responses`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              agent_reference: { type: 'agent_reference', name: AGENT_NAMES[role] },
              input, stream: true, store: false, include: ['reasoning.encrypted_content'],
            }),
            signal: AbortSignal.timeout(180_000),
          })
          if (!response.ok || !response.body) {
            const detail = await response.text()
            throw new Error(`Foundry agent ${AGENT_NAMES[role]} failed (${response.status}): ${detail.slice(0, 600)}`)
          }
          event.requestId = response.headers.get('x-request-id') ?? response.headers.get('apim-request-id') ?? response.headers.get('x-ms-request-id') ?? undefined
          const state = await readResponsesStream(response.body, role === 'supervisor' ? onProgress : undefined, raw => {
            if (captureFoundryEvent(event, raw)) publish()
          })
          if (!state.completed) throw new Error('Foundry stream ended without a completed response. No success was inferred.')
          if (state.usage) usage = {
            prompt: (usage?.prompt ?? 0) + state.usage.prompt,
            completion: (usage?.completion ?? 0) + state.usage.completion,
            total: (usage?.total ?? 0) + state.usage.total,
          }
          if (!state.output) throw new Error('Foundry omitted the response output needed for verified tool continuation.')
          input.push(...state.output)
          const calls = state.toolCalls.filter(call => call.id && call.name)
          if (!calls.length) {
            if (!state.content.trim()) throw new Error(`${AGENT_NAMES[role]} returned no answer.`)
            event.status = role === 'work-order' && proposals.length ? 'approval' : 'completed'
            event.detail = event.status === 'approval' ? 'Draft available for human review; no SQL write performed.' : 'Foundry response completed.'
            event.finishedAt = Date.now()
            publish()
            return state.content
          }
          for (const call of calls) {
            if (role === 'supervisor' && call.name === 'delegate_to_agent') {
              const { specialist, question: delegatedQuestion } = parseDelegation(call.arguments)
              const key = `${specialist}:${delegatedQuestion}`
              if (delegated.has(key) || delegated.size >= 4) throw new Error('Supervisor attempted repeated or excessive delegation.')
              delegated.add(key)
              captureApplicationEvent(event, `Handed task to ${AGENT_NAMES[specialist]}`, call.id)
              publish()
              const answer = await invoke(specialist, `Original operator request:\n${question}\n\nSupervisor task:\n${delegatedQuestion}`, event.id)
              input.push({ type: 'function_call_output', call_id: call.id, output: answer })
            } else if (role !== 'supervisor' && role !== 'fabric-iq' && call.name === 'hydro_query') {
              const parsed: unknown = JSON.parse(call.arguments)
              if (!parsed || typeof parsed !== 'object' || !('tool_name' in parsed) || !('arguments_json' in parsed)
                || typeof parsed.tool_name !== 'string' || typeof parsed.arguments_json !== 'string') throw new Error('Invalid Hydro tool call.')
              const allowed: readonly string[] = role === 'work-order' ? [...DIRECT_TOOLS, 'propose_work_order'] : DIRECT_TOOLS
              if (!allowed.includes(parsed.tool_name)) throw new Error(`Tool ${parsed.tool_name} is not permitted for ${role}.`)
              const args: ToolArguments = JSON.parse(parsed.arguments_json)
              if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Hydro tool arguments must be an object.')
              const started = Date.now()
              const step: AgentStep = { tool: parsed.tool_name, status: 'running', detail: describeToolCall(parsed.tool_name, args), summary: 'running', elapsedMs: 0, args: parsed.arguments_json }
              steps.push(step)
              captureApplicationEvent(event, `Executing ${parsed.tool_name}`, call.id)
              publish()
              publishSteps()
              try {
                const result = await runTool(parsed.tool_name, args)
                if (result.visualization) visualizations.push(result.visualization)
                if (result.model3d) models.push(result.model3d)
                const output = JSON.stringify(result.result)
                Object.assign(step, { status: 'done', elapsedMs: Date.now() - started, summary: `${result.rowCount ?? 0} returned rows`, query: result.query, result: output })
                captureApplicationEvent(event, `${parsed.tool_name} completed${result.rowCount === undefined ? '' : `: ${result.rowCount} rows`}`, call.id)
                input.push({ type: 'function_call_output', call_id: call.id, output })
              } catch (error) {
                Object.assign(step, { status: 'error', elapsedMs: Date.now() - started, error: error instanceof Error ? error.message : 'Tool execution failed.' })
                captureApplicationEvent(event, `${parsed.tool_name} failed`, call.id, true)
                throw error
              } finally { publishSteps(); publish() }
            } else {
              throw new Error(`Unexpected client tool ${call.name} from ${AGENT_NAMES[role]}.`)
            }
          }
        }
        throw new Error(`${AGENT_NAMES[role]} exceeded its six-round execution budget.`)
      } catch (error) {
        event.status = 'error'
        event.detail = error instanceof Error ? error.message : 'Foundry invocation failed.'
        event.finishedAt = Date.now()
        captureApplicationEvent(event, event.detail, undefined, true)
        publish()
        throw error
      }
    }
    const text = await invoke('supervisor', question)
    history = [...history, { role: 'user' as const, content: question }, { role: 'assistant' as const, content: text }].slice(-8)
    return { text, usage, steps, orchestrationEvents: events, proposals, visualizations, models }
  } finally { busy = false }
}
