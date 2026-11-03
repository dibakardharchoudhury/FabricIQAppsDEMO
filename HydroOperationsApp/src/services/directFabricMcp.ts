import type { AgentArtifact } from './assistantStream'
import type { DirectAccess, DirectAnswer } from './directFabric'
import Papa from 'papaparse'
import { readAnswerDatasets, type AnswerDataset } from './copilot/answerPresentation.ts'

const MAX_BYTES = 5_000_000
const identity = (value: string) => {
  if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value)) throw new Error('Direct Fabric requires verified workspace and Data Agent identities.')
  return value
}
type RecordValue = Record<string, unknown>
const record = (value: unknown): RecordValue => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The published Data Agent returned malformed native evidence.')
  return value as RecordValue
}

function inlineTables(text: string) {
  const tables: AnswerDataset[] = []
  let fence: string | undefined
  const lines = text.split(/\r?\n/)
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]
    if (fence) {
      if (new RegExp(`^ {0,3}${fence[0]}{${fence.length},}\\s*$`).test(line)) fence = undefined
      continue
    }
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/)
    if (marker) { fence = marker[1]; continue }
    if (!line.includes('|') || !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[index + 1] ?? '')) continue
    const block = [line, lines[++index]]
    while (lines[index + 1]?.includes('|')) block.push(lines[++index])
    tables.push(...readAnswerDatasets(block.join('\n')).datasets)
  }
  return tables
}

/** Published MCP questions are independent; transport sessions are not conversation memory. */
export class DirectFabricMcpConversation {
  private readonly access: (signal: AbortSignal) => Promise<DirectAccess>
  private readonly fetcher: typeof fetch
  private readonly onIdentity?: (key: string) => void
  private key?: string
  private running = false
  private artifacts: string[] = []
  constructor(access: (signal: AbortSignal) => Promise<DirectAccess>, fetcher: typeof fetch = fetch, onIdentity?: (key: string) => void) {
    this.access = access
    this.fetcher = fetcher.bind(globalThis)
    this.onIdentity = onIdentity
  }
  reset() {
    for (const url of this.artifacts) URL.revokeObjectURL(url)
    this.artifacts = []
  }
  async ask(question: string, signal: AbortSignal, progress?: (text: string) => void): Promise<DirectAnswer> {
    if (this.running) throw new Error('A direct Fabric request is already running.')
    this.running = true
    const bounded = AbortSignal.any([signal, AbortSignal.timeout(5 * 60_000)])
    const created: string[] = []
    let close: (() => Promise<void>) | undefined
    let phase = 'initialization'
    try {
      const access = await this.access(bounded)
      bounded.throwIfAborted()
      if (this.key !== access.key) {
        this.reset()
        this.key = access.key
        this.onIdentity?.(access.key)
      }
      bounded.throwIfAborted()
      const endpoint = `https://api.fabric.microsoft.com/v1/mcp/workspaces/${identity(access.workspaceId)}/dataagents/${identity(access.dataAgentId)}/agent`
      const [{ Client }, { StreamableHTTPClientTransport }] = await Promise.all([
        import('@modelcontextprotocol/sdk/client/index.js'),
        import('@modelcontextprotocol/sdk/client/streamableHttp.js'),
      ])
      const client = new Client({ name: 'hydro-operations-direct', version: '1' }, { capabilities: {} })
      close = () => client.close()
      const transport = new StreamableHTTPClientTransport(new URL(endpoint), {
        requestInit: { headers: { Authorization: `Bearer ${access.fabricToken}` }, cache: 'no-store', redirect: 'error' },
        reconnectionOptions: { maxRetries: 0, initialReconnectionDelay: 1000, maxReconnectionDelay: 1000, reconnectionDelayGrowFactor: 1 },
        fetch: async (url, init) => {
          try {
            return await this.fetcher(url, { ...init, cache: 'no-store', redirect: 'error',
              signal: init?.signal ? AbortSignal.any([bounded, init.signal]) : bounded })
          } catch (error) {
            if (bounded.aborted) throw bounded.reason
            if (init?.signal?.aborted) throw init.signal.reason
            if (!(error instanceof TypeError)) throw error
            throw new Error(`Direct Fabric MCP ${phase} failed before an HTTP response (${init?.method ?? 'GET'}). `
              + `${error.message} Check the connection, browser policy or blocked redirects. No Foundry fallback or proxy was used.`, { cause: error })
          }
        },
      })
      await client.connect(transport, { signal: bounded, timeout: 30_000 })
      phase = 'tool discovery'
      const tools = await client.listTools(undefined, { signal: bounded, timeout: 30_000 })
      if (tools.tools.length !== 1) throw new Error('Expected exactly one tool on the verified published Data Agent.')
      const tool = tools.tools[0]
      const properties = tool.inputSchema.properties ?? {}
      const argumentsList = Object.keys(properties)
      if (argumentsList.length !== 1 || record(properties[argumentsList[0]]).type !== 'string'
        || tool.inputSchema.required?.some(key => key !== argumentsList[0])) {
        throw new Error('The published Data Agent question schema is unsupported. No undeclared conversation arguments were sent.')
      }
      phase = 'question'
      const result = await client.callTool({ name: tool.name, arguments: { [argumentsList[0]]: question } }, undefined, {
        signal: bounded, timeout: 5 * 60_000, maxTotalTimeout: 5 * 60_000, resetTimeoutOnProgress: false,
      })
      const content = result.content
      if (!Array.isArray(content)) throw new Error('The published Data Agent returned no native content collection.')
      if (new TextEncoder().encode(JSON.stringify(result)).length > MAX_BYTES) throw new Error('Native MCP evidence exceeds the bounded size limit.')
      const blocks = content.map(record)
      const text = blocks.filter(block => block.type === 'text' && typeof block.text === 'string').map(block => block.text as string).join('\n')
      if (result.isError) throw new Error(text || 'The published Data Agent returned a native MCP error.')
      if (!text.trim()) throw new Error('The published Data Agent returned no native text answer. No replacement answer was generated.')
      const artifacts: AgentArtifact[] = []
      const nativeTables: AnswerDataset[] = []
      const nativeTableIssues: string[] = []
      const renderedTables = inlineTables(text)
      const file = (fileId: string, name: string, blob: Blob, image = false) => {
        if (blob.size > MAX_BYTES || artifacts.length >= 20) throw new Error('Native MCP artifacts exceed the bounded evidence limit.')
        const url = URL.createObjectURL(blob)
        created.push(url)
        artifacts.push({ fileId, name, url, kind: image ? 'image' : 'file' })
      }
      for (const [index, block] of blocks.entries()) {
        if (block.type === 'resource') {
          const resource = record(block.resource)
          const uri = typeof resource.uri === 'string' ? resource.uri : `native-resource-${index}`
          const title = typeof resource._meta === 'object' && resource._meta ? record(resource._meta).title : undefined
          const name = uri.split('/').pop() || (typeof title === 'string' ? title : `native-resource-${index}`)
          const mime = typeof resource.mimeType === 'string' ? resource.mimeType : 'application/octet-stream'
          const blob = typeof resource.text === 'string' ? new Blob([resource.text], { type: mime })
            : typeof resource.blob === 'string' ? new Blob([Uint8Array.from(atob(resource.blob), c => c.charCodeAt(0))], { type: mime }) : undefined
          if (blob) {
            file(uri, name, blob, mime.startsWith('image/'))
            if (/^text\/csv(?:;|$)/i.test(mime) || /\.csv$/i.test(name)) {
              let csv: string
              try { csv = new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer()) }
              catch (error) {
                if (!(error instanceof TypeError)) throw error
                nativeTableIssues.push(`${name}: the returned CSV is not valid UTF-8. No values were replaced; the original file remains available below.`)
                continue
              }
              const parsed = Papa.parse<string[]>(csv, { delimiter: ',' })
              if (/[\r\n]$/.test(csv) && parsed.data.at(-1)?.length === 1 && parsed.data.at(-1)?.[0] === '') parsed.data.pop()
              const [columns, ...rows] = parsed.data
              if (parsed.errors.length || !columns?.length || columns.some(column => !column.trim())
                || new Set(columns).size !== columns.length || rows.some(row => row.length !== columns.length)) {
                nativeTableIssues.push(`${name}: the returned CSV is malformed or has inconsistent columns. No rows were guessed; the original file remains available below.`)
              } else if (![...renderedTables, ...nativeTables].some(dataset =>
                JSON.stringify(dataset.columns) === JSON.stringify(columns) && JSON.stringify(dataset.rows) === JSON.stringify(rows))) {
                nativeTables.push({ format: 'csv', title: name, columns, rows, csv })
              }
            }
          }
        } else if (block.type === 'image' && typeof block.data === 'string' && typeof block.mimeType === 'string') {
          file(`native-image-${index}`, `native-image-${index}.${block.mimeType.split('/')[1]}`, new Blob([Uint8Array.from(atob(block.data), c => c.charCodeAt(0))], { type: block.mimeType }), true)
        }
      }
      file('native-mcp-evidence', 'native-mcp-evidence.json', new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }))
      const nativeTextSha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))), byte => byte.toString(16).padStart(2, '0')).join('')
      bounded.throwIfAborted()
      this.artifacts.push(...created)
      progress?.(text)
      return { text, artifacts, nativeTables, nativeTableIssues, nativeTextSha256, continuationSupported: false }
    } catch (error) {
      for (const url of created) URL.revokeObjectURL(url)
      if (bounded.aborted) throw bounded.reason
      if (error instanceof Error && 'code' in error && typeof error.code === 'number'
        && Number.isInteger(error.code) && error.code >= 400 && error.code <= 599) {
        throw new Error(`Direct Fabric MCP ${phase} failed (HTTP ${error.code}): ${error.message}`, { cause: error })
      }
      throw error
    } finally {
      await close?.().catch(() => undefined)
      this.running = false
    }
  }
}
