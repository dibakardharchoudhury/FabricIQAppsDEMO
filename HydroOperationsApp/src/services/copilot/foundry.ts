import type { AgentAnswer, AgentVisualization } from '../assistantStream.ts'
import { foundryToken } from '../fabric.ts'
import type { Asset3DModelRecord } from '../rayfin.ts'
import { catalogPrompt } from './catalog.ts'
import { readChatStream } from './chatStream.ts'
import { loadCopilotSettings, renderSystemPrompt, type CopilotSettings } from './settings.ts'
import { buildToolDefinitions, createToolRuntime, describeToolCall, type ToolArguments } from './tools.ts'

export type AgentStepStatus = 'running' | 'done' | 'error'
export type AgentStep = {
  tool: string
  status: AgentStepStatus
  detail: string
  summary: string
  query?: string
  args?: string
  // Kept for the chat's Copy action; already capped by truncateForModel.
  result?: string
  elapsedMs: number
  error?: string
}
export type FoundryAnswer = AgentAnswer & { steps?: AgentStep[]; models?: Asset3DModelRecord[] }

const MAX_TOOL_ROUNDS = 6
const MAX_HISTORY_MESSAGES = 8

/** Endpoint and deployment come from Administration, seeded from rayfin/.env, so they can be
 *  repointed at another model without a rebuild. */
export function isFoundryConfigured() {
  const settings = loadCopilotSettings()
  return Boolean(settings.endpoint && settings.deployment)
}

type ChatMessage =
  | { role: 'system' | 'user'; content: string }
  | { role: 'assistant'; content: string | null; tool_calls?: Array<{ id: string; type: 'function'; function: { name: string; arguments: string } }> }
  | { role: 'tool'; tool_call_id: string; content: string }

// Only completed user/assistant text turns are replayed; tool traffic is dropped so a long
// session cannot push the context window over the limit.
let history: ChatMessage[] = []

export function resetFoundryConversation() {
  history = []
}

function summarize(outcome: { rowCount?: number }): string {
  return outcome.rowCount === undefined ? 'done' : `${outcome.rowCount} row${outcome.rowCount === 1 ? '' : 's'}`
}

// Some deployments (e.g. vLLM-backed serverless models like Phi-4-mini-reasoning) reject
// tool_choice:"auto" unless the server was started with --enable-auto-tool-choice; that is a
// deployment-side flag the client cannot set. Detected so the caller can retry without tools.
class FoundryToolsUnsupportedError extends Error {}

// The Foundry portal's "endpoint" copy box sometimes shows a full API URL (e.g. ending in
// `/openai/v1/responses` or `/openai/v1/chat/completions`) rather than the bare resource origin
// this app expects; take just the origin so pasting either form still works.
function resourceOrigin(endpoint: string): string {
  try { return new URL(endpoint).origin }
  catch { return endpoint.replace(/\/$/, '') }
}

async function streamCompletion(settings: CopilotSettings, token: string, messages: ChatMessage[], tools: ReturnType<typeof buildToolDefinitions>, onText?: (text: string) => void) {
  const base = resourceOrigin(settings.endpoint)
  const response = await fetch(`${base}/openai/deployments/${settings.deployment}/chat/completions?api-version=${settings.apiVersion}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages,
      ...(tools.length ? { tools, tool_choice: 'auto' } : {}),
      // No `temperature`: the gpt-5 family rejects any value but the default.
      stream: true,
      stream_options: { include_usage: true },
    }),
  })
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => '')
    if (response.status === 401 || response.status === 403) {
      throw new Error('Azure AI Foundry rejected the sign-in. The account needs the "Cognitive Services OpenAI User" role on the Foundry resource.')
    }
    if (response.status === 400 && tools.length && /tool[_-]choice|tool-call-parser/i.test(detail)) {
      throw new FoundryToolsUnsupportedError(detail)
    }
    throw new Error(`Azure AI Foundry request failed (${response.status}). ${detail.slice(0, 300)}`)
  }
  return readChatStream(response.body, onText)
}

export async function askFoundryCopilot(
  question: string,
  onProgress?: (text: string) => void,
  onSteps?: (steps: AgentStep[]) => void,
): Promise<FoundryAnswer> {
  if (!isFoundryConfigured()) {
    return { text: 'The Azure AI Foundry copilot is not configured. Set the endpoint and deployment under Administration → Foundry Copilot.' }
  }
  const token = await foundryToken(true)
  if (!token) throw new Error('Azure AI Foundry sign-in is required.')

  const settings = loadCopilotSettings()
  const tools = buildToolDefinitions(settings)
  const runTool = createToolRuntime(settings)
  const messages: ChatMessage[] = [
    { role: 'system', content: renderSystemPrompt(settings, catalogPrompt(settings)) },
    ...history,
    { role: 'user', content: question },
  ]
  const steps: AgentStep[] = []
  const visualizations: AgentVisualization[] = []
  const models: Asset3DModelRecord[] = []
  let usage: FoundryAnswer['usage']
  let toolsForRequest = tools
  let toolsUnsupported = false
  const publish = () => onSteps?.(steps.map(step => ({ ...step })))

  for (let iteration = 0; iteration < MAX_TOOL_ROUNDS; iteration++) {
    let state
    try {
      state = await streamCompletion(settings, token, messages, toolsForRequest, onProgress)
    } catch (error) {
      // Deployment rejects tool_choice; fall back to a plain chat completion for the rest of this turn.
      if (error instanceof FoundryToolsUnsupportedError && toolsForRequest.length) {
        toolsForRequest = []
        toolsUnsupported = true
        state = await streamCompletion(settings, token, messages, toolsForRequest, onProgress)
      } else {
        throw error
      }
    }
    if (state.usage) {
      usage = usage
        ? { prompt: usage.prompt + state.usage.prompt, completion: usage.completion + state.usage.completion, total: usage.total + state.usage.total }
        : state.usage
    }
    const calls = state.toolCalls.filter(call => call.id && call.name)
    if (!calls.length) {
      const note = toolsUnsupported
        ? 'This model deployment does not support tool calling, so the answer below is general knowledge only \u2014 no live data was queried. Switch to a tool-calling model under Administration \u2192 Foundry Copilot for data-backed answers.\n\n'
        : ''
      const text = note + (state.content.trim() || 'The copilot returned no answer.')
      const turn: ChatMessage[] = [{ role: 'user', content: question }, { role: 'assistant', content: text }]
      history = [...history, ...turn].slice(-MAX_HISTORY_MESSAGES)
      return {
        text,
        usage,
        visualizations: visualizations.length ? visualizations : undefined,
        models: models.length ? models : undefined,
        steps: steps.length ? steps : undefined,
      }
    }

    messages.push({
      role: 'assistant',
      content: state.content || null,
      tool_calls: calls.map(call => ({ id: call.id, type: 'function', function: { name: call.name, arguments: call.arguments || '{}' } })),
    })

    for (const call of calls) {
      const startedAt = Date.now()
      let args: ToolArguments = {}
      try { args = call.arguments ? JSON.parse(call.arguments) as ToolArguments : {} } catch { /* reported below */ }
      // Publish the step before awaiting so the chat shows what is running, not just what finished.
      const step: AgentStep = {
        tool: call.name,
        status: 'running',
        detail: describeToolCall(call.name, args),
        summary: 'running…',
        args: call.arguments && call.arguments !== '{}' ? call.arguments : undefined,
        elapsedMs: 0,
      }
      steps.push(step)
      publish()
      try {
        const outcome = await runTool(call.name, args)
        if (outcome.visualization) visualizations.push(outcome.visualization)
        if (outcome.model3d) models.push(outcome.model3d)
        const payload = JSON.stringify(outcome.result)
        Object.assign(step, { status: 'done', summary: summarize(outcome), query: outcome.query, result: payload, elapsedMs: Date.now() - startedAt })
        publish()
        messages.push({ role: 'tool', tool_call_id: call.id, content: payload })
      } catch (error) {
        // Feed the failure back so the model can correct itself instead of aborting the turn.
        const message = error instanceof Error ? error.message : 'The tool call failed.'
        Object.assign(step, { status: 'error', summary: 'failed', error: message, elapsedMs: Date.now() - startedAt })
        publish()
        messages.push({ role: 'tool', tool_call_id: call.id, content: JSON.stringify({ error: message }) })
      }
    }
  }

  // A tool result on the final round still needs one tool-free completion so the model can
  // synthesize it instead of returning an iteration-limit error.
  const finalState = await streamCompletion(settings, token, messages, [], onProgress)
  if (finalState.usage) {
    usage = usage
      ? { prompt: usage.prompt + finalState.usage.prompt, completion: usage.completion + finalState.usage.completion, total: usage.total + finalState.usage.total }
      : finalState.usage
  }
  const text = finalState.content.trim() || 'The copilot returned no answer after completing its data queries.'
  const turn: ChatMessage[] = [{ role: 'user', content: question }, { role: 'assistant', content: text }]
  history = [...history, ...turn].slice(-MAX_HISTORY_MESSAGES)
  return {
    text,
    usage,
    visualizations: visualizations.length ? visualizations : undefined,
    models: models.length ? models : undefined,
    steps,
  }
}
