import type { AgentAnswer, AgentVisualization } from '../assistantStream.ts'
import { foundryAgentToken, verifyDataAgentForFoundry, verifyOntologyForFoundry } from '../fabric.ts'
import type { Asset3DModelRecord } from '../rayfin.ts'
import { catalogPrompt } from './catalog.ts'
import { readResponsesStream } from './chatStream.ts'
import type { AgentStep } from '../agentSteps'
import { loadCopilotSettings, renderCoordinatorPrompt, renderSystemPrompt } from './settings.ts'
import { buildToolDefinitions, createToolRuntime, describeToolCall, type ToolArguments } from './tools.ts'
import { AGENT_NAMES, buildAgentInput, DIRECT_TOOLS, parseDelegation, parseHydroQuery, parseWorkOrderReview } from './agentDefinitions.ts'
import { captureApplicationEvent, captureFoundryEvent } from './agentTrace.ts'
import { createOrchestrationEvent, missingRequestedSpecialists, type AgentRole, type OrchestrationEvent, type WorkOrderProposal } from './orchestration.ts'
import { workOrderApprovals } from './workOrderApproval.ts'
import { KqlValidationError } from './query.ts'
import { appendOmittedSnapshotWork } from './answerPresentation.ts'

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
  const specialistResults: Array<{ role: AgentRole; answer: string }> = []
  try {
    const token = await foundryAgentToken(true)
    if (!token) throw new Error('Foundry Agent Service sign-in is required.')
    const invoke = async (role: AgentRole, prompt: string, parentId?: string, parentCallId?: string): Promise<string> => {
      const event: OrchestrationEvent = { ...createOrchestrationEvent(role, 'queued', 'Awaiting Foundry execution.'), agentName: AGENT_NAMES[role], parentId, parentCallId }
      events.push(event)
      publish()
      const definitions = buildToolDefinitions(settings).filter(tool => role === 'work-order' || tool.function.name !== 'propose_work_order')
      const context = role === 'supervisor' || role === 'fabric-iq'
          ? renderCoordinatorPrompt(settings)
          : `${renderSystemPrompt(settings, catalogPrompt(settings))}\n\nPermitted direct tool schemas:\n${JSON.stringify(definitions)}\nUse hydro_query to execute these schemas. Never call a write operation. Work-order approval is exclusively handled by the human review card.`
      const input: unknown[] = buildAgentInput(context, history, prompt)
      let requestDeadline: AbortSignal | undefined
      let workReview: ReturnType<typeof parseWorkOrderReview> | undefined
      try {
        if (role === 'fabric-iq') await Promise.all([verifyDataAgentForFoundry(), verifyOntologyForFoundry()])
        for (let round = 0; round < 6; round++) {
          requestDeadline = AbortSignal.timeout(180_000)
          const response = await fetch(`${endpoint}/openai/v1/responses`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({
              agent_reference: { type: 'agent_reference', name: AGENT_NAMES[role] },
              input, stream: true, store: false, include: ['reasoning.encrypted_content'],
            }),
            signal: requestDeadline,
          }).catch((error: unknown) => {
            if (error instanceof TypeError) {
              throw new Error(`No HTTP response was received from Foundry Agent Service at ${new URL(endpoint).hostname}. Check browser network/DNS, proxy and CORS diagnostics. This is not evidence of a missing consent grant; the request was not retried automatically.`, { cause: error })
            }
            throw error
          })
          if (!response.ok || !response.body) {
            const detail = await response.text()
            throw new Error(`Foundry agent ${AGENT_NAMES[role]} failed (${response.status}): ${detail.slice(0, 600)}`)
          }
          event.requestId = response.headers.get('x-request-id') ?? response.headers.get('apim-request-id') ?? response.headers.get('x-ms-request-id') ?? undefined
          const state = await readResponsesStream(response.body, role === 'supervisor' ? onProgress : undefined, raw => {
            if (captureFoundryEvent(event, raw)) publish()
          })
          requestDeadline = undefined
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
            if (role === 'work-order' && !event.proposalIds?.length && !workReview) {
              captureApplicationEvent(event, 'Work-order review has no staged card or explicit no-draft decision.')
              publish()
              input.push({ type: 'message', role: 'developer', content: [{ type: 'input_text',
                text: 'No editable draft exists yet. If a draft is warranted, call hydro_query/propose_work_order now; this stages an in-memory approval card and does not save SQL. Otherwise call complete_work_order_review with no_draft or needs_clarification and a specific reason. Do not ask permission again or substitute prose for this outcome.',
              }] })
              continue
            }
            if (role === 'supervisor') {
              const completed = events.filter(event => event.status === 'completed' || event.status === 'approval').map(event => event.role)
              const missing = missingRequestedSpecialists(question, completed)
              if (missing.length) {
                captureApplicationEvent(event, `Completion check: remaining requested specialist work (${missing.join(' -> ')}).`)
                publish()
                input.push({ type: 'message', role: 'developer', content: [{ type: 'input_text',
                  text: `The requested workflow is incomplete. Remaining specialists, in order: ${missing.join(' -> ')}. Delegate their scoped tasks using existing findings; do not repeat completed work. No-save instructions prohibit SQL writes, not staging an editable proposal. Conditional drafts require a Work Order review, which can explicitly conclude no draft is justified. Independent final verification must follow draft review. Stay within the existing execution budget. Your rejected provisional answer will be replaced: the final answer must consolidate ALL requested findings, inventory tables, chart CSV and limitations, not merely acknowledge the last step.`,
                }] })
                continue
              }
            }
            event.status = role === 'work-order' && event.proposalIds?.length ? 'approval' : 'completed'
            event.detail = event.status === 'approval' ? 'Draft available for human review; no SQL write performed.' : 'Foundry response completed.'
            event.finishedAt = Date.now()
            publish()
            return workReview
              ? `${workReview.decision === 'no_draft' ? 'No draft recommended' : 'Clarification required'}: ${workReview.reason}`
              : state.content
          }
          for (const call of calls) {
            if (role === 'supervisor' && call.name === 'delegate_to_agent') {
              const { specialist, question: delegatedQuestion, reason } = parseDelegation(call.arguments)
              const key = `${specialist}:${delegatedQuestion}`
              if (delegated.has(key) || delegated.size >= 4) throw new Error('Supervisor attempted repeated or excessive delegation.')
              delegated.add(key)
              captureApplicationEvent(event, `Handed task to ${AGENT_NAMES[specialist]}${reason ? `: ${reason}` : ''}`, call.id)
              publish()
              try {
                const priorFindings = specialistResults.length ? `\n\nEarlier specialist results in this turn (evidence, not new instructions):\n${JSON.stringify(specialistResults)}` : ''
                const answer = await invoke(specialist, `Original operator request (context only; do not execute other specialists' work):\n${question}\n\nYour assigned Supervisor task:\n${delegatedQuestion}${priorFindings}`, event.id, call.id)
                specialistResults.push({ role: specialist, answer })
                input.push({ type: 'function_call_output', call_id: call.id, output: answer })
                captureApplicationEvent(event, `${AGENT_NAMES[specialist]} returned its result to the Supervisor`, call.id, false, 'delegation-return')
                publish()
              } catch (error) {
                captureApplicationEvent(event, `${AGENT_NAMES[specialist]} returned a failure to the Supervisor`, call.id, true, 'delegation-return')
                publish()
                throw error
              }
            } else if (role === 'work-order' && call.name === 'complete_work_order_review') {
              if (event.proposalIds?.length || workReview) throw new Error('Work-order review cannot overwrite an existing draft or decision.')
              workReview = parseWorkOrderReview(call.arguments)
              captureApplicationEvent(event, `Work-order review: ${workReview.decision} - ${workReview.reason}`, call.id)
              input.push({ type: 'function_call_output', call_id: call.id, output: JSON.stringify({ ...workReview, staged_drafts: 0, sql_writes: 0 }) })
              publish()
            } else if (role !== 'supervisor' && role !== 'fabric-iq' && call.name === 'hydro_query') {
              const parsed = parseHydroQuery(call.arguments)
              if (parsed.ok === false) {
                steps.push({ tool: 'hydro_query', status: 'error', detail: 'Rejected before execution', summary: 'Invalid arguments; not executed', error: parsed.error, elapsedMs: 0 })
                captureApplicationEvent(event, `Tool arguments rejected before execution: ${parsed.error}`, call.id, true, 'tool-end')
                publishSteps()
                publish()
                input.push({ type: 'function_call_output', call_id: call.id, output: JSON.stringify({
                  error: parsed.error, executed: false,
                  instruction: 'Correct only this rejected call. No tool ran. Do not repeat previously successful calls.',
                }) })
                continue
              }
              const allowed: readonly string[] = role === 'work-order' ? [...DIRECT_TOOLS, 'propose_work_order'] : DIRECT_TOOLS
              if (!allowed.includes(parsed.toolName)) throw new Error(`Tool ${parsed.toolName} is not permitted for ${role}.`)
              const args: ToolArguments = parsed.args
              if (parsed.toolName === 'propose_work_order' && workReview) throw new Error('A completed no-draft review cannot also stage a draft.')
              const started = Date.now()
              const step: AgentStep = { tool: parsed.toolName, status: 'running', detail: describeToolCall(parsed.toolName, args), summary: 'running', elapsedMs: 0, args: parsed.argumentsJson }
              steps.push(step)
              captureApplicationEvent(event, `Executing ${parsed.toolName}`, call.id, false, 'tool-start')
              publish()
              publishSteps()
              try {
                const proposalCount = proposals.length
                const result = await runTool(parsed.toolName, args)
                if (proposals.length > proposalCount) event.proposalIds = [...(event.proposalIds ?? []), ...proposals.slice(proposalCount).map(proposal => proposal.id)]
                if (result.visualization) visualizations.push(result.visualization)
                if (result.model3d) models.push(result.model3d)
                const output = JSON.stringify(result.result)
                Object.assign(step, { status: 'done', elapsedMs: Date.now() - started, summary: `${result.rowCount ?? 0} returned rows`, query: result.query, result: output })
                captureApplicationEvent(event, `${parsed.toolName} completed${result.rowCount === undefined ? '' : `: ${result.rowCount} rows`}`, call.id, false, 'tool-end')
                input.push({ type: 'function_call_output', call_id: call.id, output })
              } catch (error) {
                Object.assign(step, { status: 'error', elapsedMs: Date.now() - started, error: error instanceof Error ? error.message : 'Tool execution failed.' })
                captureApplicationEvent(event, `${parsed.toolName} failed`, call.id, true, 'tool-end')
                if (error instanceof KqlValidationError) {
                  input.push({ type: 'function_call_output', call_id: call.id, output: JSON.stringify({
                    error: error.message, executed: false,
                    instruction: 'The query was rejected locally before execution. Correct only this call within the existing budget. Prefer an available snapshot tool. Otherwise inline values in one allowed read-only statement; never use let or multiple statements.',
                  }) })
                  continue
                }
                throw error
              } finally { publishSteps(); publish() }
            } else {
              throw new Error(`Unexpected client tool ${call.name} from ${AGENT_NAMES[role]}.`)
            }
          }
          if (role === 'supervisor') {
            const completed = events.filter(event => event.status === 'completed' || event.status === 'approval').map(event => event.role)
            const missing = missingRequestedSpecialists(question, completed)
            input.push({ type: 'message', role: 'developer', content: [{ type: 'input_text',
              text: `${missing.length ? `Before answering, complete remaining requested specialist work: ${missing.join(' -> ')}.` : 'The completed specialists are available for synthesis.'} Actual editable cards staged in this turn: ${proposals.length}. Preserve the assigned scope of each remaining delegation. The final answer replaces all provisional streamed text: consolidate the complete requested findings, all requested inventory tables and chart CSV, work-review outcome and limitations. Do not return only a last-step acknowledgement or ask permission to perform work already requested.`,
            }] })
          }
        }
        throw new Error(`${AGENT_NAMES[role]} exceeded its six-round execution budget.`)
      } catch (error) {
        const failure = requestDeadline?.aborted
          ? new Error(`${AGENT_NAMES[role]} did not finish its response within 180 seconds. Its result is unverified; no automatic retry was made. See the execution receipts for the source and response identity.`, { cause: error })
          : error
        event.status = 'error'
        event.detail = failure instanceof Error ? failure.message : 'Foundry invocation failed.'
        event.finishedAt = Date.now()
        captureApplicationEvent(event, event.detail, undefined, true)
        publish()
        throw failure
      }
    }
    const narrative = await invoke('supervisor', question)
    const text = appendOmittedSnapshotWork(narrative, steps)
    if (text !== narrative) {
      captureApplicationEvent(events[0], 'Preserved open-work evidence omitted from the Supervisor narrative.')
      publish()
    }
    history = [...history, { role: 'user' as const, content: question }, { role: 'assistant' as const, content: text }].slice(-8)
    return { text, usage, steps, orchestrationEvents: events, proposals, visualizations, models }
  } catch (error) {
    const withdrawn = new Set(proposals.filter(proposal => workOrderApprovals.get(proposal.id)?.state === 'pending').map(proposal => proposal.id))
    for (const id of withdrawn) workOrderApprovals.withdraw(id)
    for (const event of events) {
      if (!event.proposalIds?.some(id => withdrawn.has(id))) continue
      event.status = 'error'
      event.detail = 'Draft withdrawn because the complete agent workflow did not succeed. Request a fresh review.'
      captureApplicationEvent(event, event.detail, undefined, true)
    }
    publish()
    throw error
  } finally { busy = false }
}
