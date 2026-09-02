export type AgentUsage = { prompt: number; completion: number; total: number }
export type AgentAnswer = { text: string; usage?: AgentUsage }

type StreamEvent = {
  object?: string
  delta?: { content?: Array<{ text?: { value?: string } }> }
  content?: Array<{ text?: { value?: string } }>
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }
}

export async function readAssistantStream(body: ReadableStream<Uint8Array>, onProgress?: (text: string) => void): Promise<AgentAnswer> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let answer = ''
  let usage: AgentUsage | undefined
  const consumeEvent = (block: string) => {
    const data = block.split(/\r?\n/)
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart())
      .join('\n')
      .trim()
    if (!data || data === '[DONE]') return
    let event: StreamEvent
    try { event = JSON.parse(data) as StreamEvent } catch { return }
    if (event.usage && typeof event.usage.total_tokens === 'number') {
      usage = { prompt: event.usage.prompt_tokens ?? 0, completion: event.usage.completion_tokens ?? 0, total: event.usage.total_tokens }
    }
    const deltas = event.delta?.content
    if (Array.isArray(deltas)) {
      for (const part of deltas) {
        const chunk = part.text?.value
        if (chunk) { answer += chunk; onProgress?.(answer) }
      }
    } else if (event.object === 'thread.message' && Array.isArray(event.content) && !answer) {
      const full = event.content.map(part => part.text?.value ?? '').filter(Boolean).join('\n')
      if (full) { answer = full; onProgress?.(answer) }
    }
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() ?? ''
    blocks.forEach(consumeEvent)
  }
  buffer += decoder.decode()
  if (buffer.trim()) consumeEvent(buffer)
  return { text: answer, usage }
}