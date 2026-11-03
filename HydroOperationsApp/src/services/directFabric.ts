import type { AgentAnswer, AgentArtifact } from './assistantStream'
import type { AnswerDataset } from './copilot/answerPresentation'

export type NativeCitation = { title: string; url?: string; fileId?: string }
export type DirectAnswer = AgentAnswer & {
  citations?: NativeCitation[]; nativeResponseId?: string; nativeTextSha256?: string; continuationSupported?: false
  nativeTables?: AnswerDataset[]; nativeTableIssues?: string[]
}
export { DirectFabricMcpConversation as DirectFabricConversation } from './directFabricMcp'
export type DirectAccess = {
  key: string
  workspaceId: string
  dataAgentId: string
  token: () => Promise<string>
  fabricToken: string
}
type Json = Record<string, unknown>
const API = 'https://api.fabric.microsoft.com'
const VERSION = '?api-version=2024-05-01-preview'
const MAX_BYTES = 5_000_000
const object = (value: unknown): Json => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Fabric returned an invalid response envelope.')
  return value as Json
}
const guid = (value: unknown): string => {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value)) throw new Error('Fabric returned an invalid workspace, capacity or agent identity.')
  return value
}

function trustedOrigin(value: unknown, suffix: string): string {
  if (typeof value !== 'string') throw new Error('Fabric workload routing returned no origin.')
  const url = new URL(value)
  if (url.protocol !== 'https:' || !url.hostname.endsWith(suffix) || url.username || url.password
    || url.port || url.search || url.hash || url.pathname !== '/') throw new Error('Fabric returned an untrusted workload origin.')
  return url.origin
}

async function bytes(response: Response): Promise<Uint8Array> {
  if (!response.body) throw new Error('Fabric returned an empty response body.')
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      length += value.length
      if (length > MAX_BYTES) throw new Error('Fabric response exceeds the bounded evidence size limit.')
      chunks.push(value)
    }
  } finally { await reader.cancel().catch(() => undefined) }
  const result = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length }
  return result
}

/** Some Fabric runtimes return SSE even when streaming was not requested. */
export async function readNativeResponse(response: Response, progress?: (text: string) => void): Promise<Json> {
  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    return object(JSON.parse(new TextDecoder().decode(await bytes(response))))
  }
  if (!response.body) throw new Error('Fabric returned an empty response stream.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let text = ''
  let length = 0
  const consume = (block: string): Json | undefined => {
    const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    if (!data || data === '[DONE]') return
    const event = object(JSON.parse(data))
    if (event.type === 'error') throw new Error('Fabric Data Agent returned a streaming execution error.')
    if (event.type === 'response.output_text.delta' && typeof event.delta === 'string') {
      text += event.delta
      progress?.(text)
    }
    if (['response.completed', 'response.failed', 'response.incomplete'].includes(String(event.type))) return object(event.response)
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      length += value?.length ?? 0
      if (length > MAX_BYTES) throw new Error('Fabric stream exceeds the bounded evidence size limit.')
      buffer += decoder.decode(value, { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const terminal = consume(block)
        if (terminal) return terminal
      }
      if (done) {
        const terminal = consume(buffer)
        if (terminal) return terminal
        throw new Error('Fabric stream ended without a terminal response.')
      }
    }
  } finally { await reader.cancel().catch(() => undefined) }
}

export class DirectResponsesConversation {
  private key?: string
  private previousResponseId?: string
  private running = false
  private artifacts: string[] = []
  private readonly access: (signal: AbortSignal) => Promise<DirectAccess>
  private readonly fetcher: typeof fetch
  private readonly onIdentity?: (key: string) => void
  constructor(
    access: (signal: AbortSignal) => Promise<DirectAccess>,
    fetcher: typeof fetch = fetch,
    onIdentity?: (key: string) => void,
  ) { this.access = access; this.fetcher = fetcher; this.onIdentity = onIdentity }

  reset() {
    this.previousResponseId = undefined
    for (const url of this.artifacts) URL.revokeObjectURL(url)
    this.artifacts = []
  }

  async ask(question: string, signal: AbortSignal, progress?: (text: string) => void): Promise<DirectAnswer> {
    if (this.running) throw new Error('A direct Fabric request is already running.')
    this.running = true
    const bounded = AbortSignal.any([signal, AbortSignal.timeout(5 * 60_000)])
    const created: string[] = []
    try {
      const access = await this.access(bounded)
      bounded.throwIfAborted()
      if (this.key !== access.key) {
        this.reset()
        this.key = access.key
        this.onIdentity?.(access.key)
      }
      bounded.throwIfAborted()
      const workspace = guid(access.workspaceId)
      const agent = guid(access.dataAgentId)
      const token = await access.token()
      const request = async (url: string, init?: RequestInit, fabric = false): Promise<Response> => {
        let response: Response
        try {
          response = await this.fetcher(url, { ...init, signal: bounded, cache: 'no-store', redirect: 'error',
            headers: { Authorization: `Bearer ${fabric ? access.fabricToken : token}`, ActivityId: crypto.randomUUID(),
              ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
              ...init?.headers } })
        } catch (error) {
          if (bounded.aborted) throw bounded.reason
          const target = new URL(url)
          const routing = target.pathname.endsWith('/metadata/v201606/generatemwctokenv2')
          throw new Error(`Direct Fabric ${routing ? 'regional ML routing' : 'browser request'} failed (${init?.method ?? 'GET'} ${target.origin}${target.pathname}). `
            + 'Browser CORS/network access is blocked; a valid delegated token cannot bypass provider CORS. '
            + 'No answer was obtained; no Foundry fallback or proxy was used.', { cause: error })
        }
        if (!response.ok) throw new Error(`Direct Fabric request failed (HTTP ${response.status}) at ${new URL(url).origin}. Check delegated read/execute access; no fallback was used.`)
        return response
      }
      const json = async (response: Response) => object(JSON.parse(new TextDecoder().decode(await bytes(response))))
      const ws = await json(await request(`${API}/v1/workspaces/${workspace}`, undefined, true))
      if (String(ws.id).toLowerCase() !== workspace.toLowerCase()) throw new Error('Live Fabric workspace identity changed.')
      const capacity = guid(ws.capacityId)
      const cluster = await json(await request(`${API}/powerbi/globalservice/v201606/clusterDetails`))
      const clusterOrigin = trustedOrigin(cluster.clusterUrl, '.analysis.windows.net')
      const routing = await json(await request(`${clusterOrigin}/metadata/v201606/generatemwctokenv2`, {
        method: 'POST', body: JSON.stringify({ capacityObjectId: capacity, workspaceObjectId: workspace, workloadType: 'ML' }),
      }))
      const origin = trustedOrigin(typeof routing.TargetUriHost === 'string' ? `https://${routing.TargetUriHost}` : undefined, '.pbidedicated.windows.net')
      const base = `${origin}/webapi/capacities/${capacity}/workloads/ML/AISkill/Automatic/v1/workspaces/${workspace}/artifacts/${agent}`
      const headers = { Accept: 'application/json', 'x-ms-workload-resource-moniker': crypto.randomUUID(),
        'x-ms-ai-assistant-scenario': 'aiskill', 'x-ms-ai-aiskill-stage': 'production',
        'X-Taxonomy-TrafficType': 'Production', 'x-llm-service-tier': 'default' }
      let response = await readNativeResponse(await request(`${base}/responses${VERSION}`, {
        method: 'POST', headers, body: JSON.stringify({ model: 'gpt-5.1', input: question,
          ...(this.previousResponseId ? { previous_response_id: this.previousResponseId } : {}) }),
      }), progress)
      while (response.status === 'queued' || response.status === 'in_progress') {
        if (typeof response.id !== 'string' || !response.id) throw new Error('Fabric returned no response polling identity.')
        await new Promise<void>((resolve, reject) => {
          const abort = () => { clearTimeout(timer); reject(bounded.reason) }
          const timer = setTimeout(() => { bounded.removeEventListener('abort', abort); resolve() }, 1000)
          bounded.addEventListener('abort', abort, { once: true })
          if (bounded.aborted) abort()
        })
        response = await readNativeResponse(await request(`${base}/responses/${encodeURIComponent(response.id)}${VERSION}`, { headers }), progress)
      }
      if (response.status !== 'completed' || response.error) throw new Error(`Fabric Data Agent did not complete (${String(response.status)}). No replacement answer was generated.`)
      if (typeof response.id !== 'string' || !response.id || !Array.isArray(response.output)) throw new Error('Fabric returned no valid response identity or output.')
      const texts: string[] = []
      const citations: NativeCitation[] = []
      const files = new Map<string, string>()
      for (const raw of response.output) {
        const item = object(raw)
        if (item.type !== 'message' || item.role !== 'assistant' || !Array.isArray(item.content)) continue
        for (const rawBlock of item.content) {
          const block = object(rawBlock)
          if (block.type === 'refusal') throw new Error(typeof block.refusal === 'string' ? block.refusal : 'Fabric Data Agent refused the question.')
          if (typeof block.text === 'string') texts.push(block.text)
          for (const rawAnnotation of Array.isArray(block.annotations) ? block.annotations : []) {
            const annotation = object(rawAnnotation)
            if (annotation.type === 'url_citation' && typeof annotation.url === 'string') {
              const url = new URL(annotation.url)
              if (url.protocol === 'https:' || url.protocol === 'http:') citations.push({ title: String(annotation.title ?? annotation.url), url: url.href })
            }
            if (annotation.type === 'file_citation') {
              if (typeof annotation.file_id !== 'string' || !/^file_[A-Za-z0-9_-]+$/.test(annotation.file_id)) throw new Error('Fabric returned an invalid result-file identity.')
              const title = String(annotation.filename ?? annotation.file_id)
              files.set(annotation.file_id, title)
              citations.push({ title, fileId: annotation.file_id })
            }
          }
        }
      }
      if (!texts.some(text => text.trim())) throw new Error('Fabric Data Agent completed without an assistant answer.')
      if (files.size > 20) throw new Error('Fabric returned more than 20 result artifacts.')
      const artifacts: AgentArtifact[] = []
      for (const [fileId, name] of files) {
        const file = await request(`${base}/aiassistant/openai/files/${encodeURIComponent(fileId)}/content${VERSION}`, { headers })
        const data = await bytes(file)
        const url = URL.createObjectURL(new Blob([data as BlobPart], { type: file.headers.get('content-type') ?? 'application/octet-stream' }))
        created.push(url)
        artifacts.push({ fileId, name, url, kind: 'file' })
      }
      bounded.throwIfAborted()
      this.previousResponseId = response.id
      this.artifacts.push(...created)
      const text = texts.join('\n')
      progress?.(text)
      return { text, artifacts, citations, nativeResponseId: response.id }
    } catch (error) {
      for (const url of created) URL.revokeObjectURL(url)
      if (!bounded.aborted && error instanceof TypeError && /fetch|network/i.test(error.message)) {
        throw new Error('Direct Fabric published-source verification is blocked by browser CORS/network access. No Foundry fallback was used.', { cause: error })
      }
      throw error
    } finally { this.running = false }
  }
}
