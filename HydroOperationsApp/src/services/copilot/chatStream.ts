import type { AgentUsage } from '../assistantStream.ts'

export type ToolCallDraft = { id: string; name: string; arguments: string }
export type StreamState = { content: string; toolCalls: ToolCallDraft[]; usage?: AgentUsage; finishReason?: string }

type ChatChunk = {
  choices?: Array<{
    delta?: {
      content?: string | null
      tool_calls?: Array<{ index?: number; id?: string; function?: { name?: string; arguments?: string } }>
    }
    finish_reason?: string | null
  }>
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } | null
}

type ResponsesEvent = {
  type?: string
  delta?: string
  output_index?: number
  item?: { type?: string; call_id?: string; name?: string; arguments?: string; content?: Array<{ type?: string; text?: string }> }
  response?: {
    usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number }
    error?: { message?: string }
  }
}

export function createStreamState(): StreamState {
  return { content: '', toolCalls: [] }
}

/** Fold one streamed chunk into the accumulator. Tool call fragments arrive split across
 *  chunks and are keyed by `index`, so they must be merged positionally, not appended. */
export function applyChunk(state: StreamState, chunk: unknown): StreamState {
  const typed = chunk as ChatChunk
  if (typed.usage && typeof typed.usage.total_tokens === 'number') {
    state.usage = {
      prompt: typed.usage.prompt_tokens ?? 0,
      completion: typed.usage.completion_tokens ?? 0,
      total: typed.usage.total_tokens,
    }
  }
  const choice = typed.choices?.[0]
  if (!choice) return state
  if (choice.finish_reason) state.finishReason = choice.finish_reason
  if (typeof choice.delta?.content === 'string') state.content += choice.delta.content
  for (const fragment of choice.delta?.tool_calls ?? []) {
    const index = fragment.index ?? 0
    while (state.toolCalls.length <= index) state.toolCalls.push({ id: '', name: '', arguments: '' })
    const draft = state.toolCalls[index]
    if (fragment.id) draft.id = fragment.id
    if (fragment.function?.name) draft.name += fragment.function.name
    if (fragment.function?.arguments) draft.arguments += fragment.function.arguments
  }
  return state
}

/** Fold one Azure AI Responses API SSE event into the shared agent-loop state. */
export function applyResponsesEvent(state: StreamState, event: unknown): StreamState {
  const typed = event as ResponsesEvent
  if (typed.type === 'response.output_text.delta' && typeof typed.delta === 'string') {
    state.content += typed.delta
  }
  if (typed.type === 'response.output_item.added' && typed.item?.type === 'function_call') {
    const index = typed.output_index ?? state.toolCalls.length
    while (state.toolCalls.length <= index) state.toolCalls.push({ id: '', name: '', arguments: '' })
    state.toolCalls[index] = {
      id: typed.item.call_id ?? '',
      name: typed.item.name ?? '',
      arguments: typed.item.arguments ?? '',
    }
  }
  if (typed.type === 'response.function_call_arguments.delta' && typeof typed.delta === 'string') {
    const index = typed.output_index ?? 0
    while (state.toolCalls.length <= index) state.toolCalls.push({ id: '', name: '', arguments: '' })
    state.toolCalls[index].arguments += typed.delta
  }
  if (typed.type === 'response.output_item.done' && typed.item?.type === 'function_call') {
    const index = typed.output_index ?? 0
    while (state.toolCalls.length <= index) state.toolCalls.push({ id: '', name: '', arguments: '' })
    state.toolCalls[index] = {
      id: typed.item.call_id ?? state.toolCalls[index].id,
      name: typed.item.name ?? state.toolCalls[index].name,
      arguments: typed.item.arguments ?? state.toolCalls[index].arguments,
    }
  }
  if (typed.type === 'response.completed' && typed.response?.usage) {
    state.usage = {
      prompt: typed.response.usage.input_tokens ?? 0,
      completion: typed.response.usage.output_tokens ?? 0,
      total: typed.response.usage.total_tokens ?? 0,
    }
  }
  return state
}

/** Split a raw SSE buffer into complete event payloads, returning the unterminated remainder. */
export function splitSseEvents(buffer: string): { events: string[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/)
  const rest = blocks.pop() ?? ''
  const events = blocks
    .map(block => block
      .split(/\r?\n/)
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart())
      .join('\n')
      .trim())
    .filter(data => data && data !== '[DONE]')
  return { events, rest }
}

/** Consume an Azure OpenAI streaming chat completion into a single accumulated state. */
export async function readChatStream(body: ReadableStream<Uint8Array>, onText?: (text: string) => void): Promise<StreamState> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const state = createStreamState()
  let buffer = ''
  const consume = (payload: string) => {
    let chunk: unknown
    try { chunk = JSON.parse(payload) } catch { return }
    const before = state.content
    applyChunk(state, chunk)
    if (state.content !== before) onText?.(state.content)
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { events, rest } = splitSseEvents(buffer)
    buffer = rest
    events.forEach(consume)
  }
  buffer += decoder.decode()
  const { events } = splitSseEvents(`${buffer}\n\n`)
  events.forEach(consume)
  return state
}

/** Consume an Azure AI Responses API stream into the state used by the existing tool loop. */
export async function readResponsesStream(body: ReadableStream<Uint8Array>, onText?: (text: string) => void): Promise<StreamState> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const state = createStreamState()
  let buffer = ''
  const consume = (payload: string) => {
    let event: ResponsesEvent
    try { event = JSON.parse(payload) as ResponsesEvent } catch { return }
    if (event.type === 'response.failed') {
      throw new Error(event.response?.error?.message ?? 'Azure AI Foundry response failed.')
    }
    const before = state.content
    applyResponsesEvent(state, event)
    if (state.content !== before) onText?.(state.content)
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { events, rest } = splitSseEvents(buffer)
    buffer = rest
    events.forEach(consume)
  }
  buffer += decoder.decode()
  const { events } = splitSseEvents(`${buffer}\n\n`)
  events.forEach(consume)
  return state
}
