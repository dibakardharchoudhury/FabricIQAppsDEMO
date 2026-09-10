export type ChatMessage =
  | { role: 'system' | 'user'; content: string }
  | { role: 'assistant'; content: string | null; tool_calls?: Array<{ id: string; type: 'function'; function: { name: string; arguments: string } }> }
  | { role: 'tool'; tool_call_id: string; content: string }

export type ResponsesInput =
  | { role: 'system' | 'user' | 'assistant'; content: string }
  | { type: 'function_call'; call_id: string; name: string; arguments: string }
  | { type: 'function_call_output'; call_id: string; output: string }

type FunctionTool = {
  function: { name: string; description: string; parameters: unknown }
}

/** Preserve completed conversation history and active-turn tool traffic in Responses API format. */
export function buildResponsesInput(messages: ChatMessage[]): ResponsesInput[] {
  const input: ResponsesInput[] = []
  for (const message of messages) {
    if (message.role === 'tool') {
      input.push({ type: 'function_call_output', call_id: message.tool_call_id, output: message.content })
      continue
    }
    if (message.role === 'assistant' && message.tool_calls?.length) {
      if (message.content) input.push({ role: 'assistant', content: message.content })
      input.push(...message.tool_calls.map(call => ({
        type: 'function_call' as const,
        call_id: call.id,
        name: call.function.name,
        arguments: call.function.arguments,
      })))
      continue
    }
    if (message.content) input.push({ role: message.role, content: message.content })
  }
  return input
}

export function buildResponsesRequest(deployment: string, messages: ChatMessage[], tools: FunctionTool[]) {
  return {
    model: deployment,
    input: buildResponsesInput(messages),
    ...(tools.length ? {
      tools: tools.map(tool => ({ type: 'function' as const, ...tool.function })),
      tool_choice: 'auto' as const,
    } : {}),
    stream: true as const,
  }
}

export function appendCompletedTurn(history: ChatMessage[], question: string, answer: string, limit = 8): ChatMessage[] {
  return [
    ...history,
    { role: 'user' as const, content: question },
    { role: 'assistant' as const, content: answer },
  ].slice(-limit)
}
