import { lazy, memo, Suspense, useEffect, useId, useMemo, useRef, useState } from 'react'
import { BarChart3, Bot, Box, Check, Copy, Download, ExternalLink, LineChart, Maximize2, Minimize2, PieChart, Send, SquarePen, Wrench } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AgentArtifact, AgentVisualization } from '../services/fabric'
import type { Asset3DModelRecord } from '../services/rayfin'
import type { AgentStep } from '../services/copilot/foundry'
import type { OrchestrationEvent, WorkOrderProposal } from '../services/copilot/orchestration'
import { WorkOrderApprovalCard } from './WorkOrderApprovalCard'
import { AgentCrewTrace } from './AgentCrewTrace'
import { relatedSuggestions, stripOptionsMarker, suggestionLabel } from '../services/copilot/suggestions'
import { hideRenderedData, answerSections, operatorNarrative } from '../services/copilot/answerPresentation'
import type { CopilotEngine } from '../ui-shared/hooks/useHydroOperationsData'
import { AnswerDashboard } from './AnswerDashboard'
import { AgentVisualizationView } from './AgentVisualizationView'
import { CopilotStreamCursor, CopilotThinking } from './CopilotThinking'
import { VoiceInput } from './VoiceInput'
import { useExpandedView } from '../ui-shared/hooks/useExpandedView'

// Lazy so three.js / model-viewer only load when the agent actually renders a GLB.
const AssetModelViewer = lazy(() => import('./AssetModelViewer').then(module => ({ default: module.AssetModelViewer })))
const canRenderModel = (format?: string) => Boolean(format && ['GLB', 'GLTF'].includes(format.toUpperCase()))

export type CopilotMessage = {
  role: 'user' | 'agent'
  text: string
  artifacts?: AgentArtifact[]
  visualizations?: AgentVisualization[]
  models?: Asset3DModelRecord[]
  steps?: AgentStep[]
  orchestrationEvents?: OrchestrationEvent[]
  proposals?: WorkOrderProposal[]
  backendOwned?: boolean
  meta?: { elapsedMs: number; tokens?: number }
}

type CopilotExperienceProps = {
  messages: CopilotMessage[]
  busy: boolean
  engine: CopilotEngine
  foundryAvailable: boolean
  battleEnabled?: boolean
  onSend: (question: string) => void
  onReset: () => void
  onEngineChange?: (engine: CopilotEngine) => void
  onBattle?: () => void
}

const ENGINE_LABELS: Record<CopilotEngine, { name: string; source: string }> = {
  'data-agent': { name: 'Data Agent', source: 'Fabric Data Agent' },
  foundry: { name: 'Foundry', source: 'Azure AI Foundry · Lakehouse + Eventhouse' },
}

const PROMPTS: Record<CopilotEngine, string[]> = {
  'data-agent': [
    'Show all open work orders as a table with the affected asset, priority, and status.',
    'List all equipment with manufacturer, model, and criticality.',
    'Which assets have the most open work orders? Give a ranked table and chart.',
    'Summarize the facilities with their type, country, and number of assets.',
  ],
  foundry: [
    'Which turbines had BAD or UNCERTAIN telemetry quality in the last 6 hours?',
    'Chart average power output per station over the last 24 hours.',
    'List the most critical equipment that has an open work order.',
    'Which spare parts are at or below their reorder level?',
  ],
}

export function CopilotExperience({ messages, busy, engine, foundryAvailable, battleEnabled, onSend, onReset, onEngineChange, onBattle }: CopilotExperienceProps) {
  const [question, setQuestion] = useState('')
  const [flowExpanded, setFlowExpanded] = useState(false)
  const [flowWidth, setFlowWidth] = useState(320)
  const chatView = useExpandedView()
  const prompts = useMemo(() => PROMPTS[engine], [engine])
  const listRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const conversationEvents = messages.flatMap(message => message.orchestrationEvents ?? [])
  const conversationProposals = messages.flatMap(message => message.proposals ?? [])
  const currentEventIds = messages[messages.length - 1]?.orchestrationEvents?.map(event => event.id) ?? []
  // Follow new content only while the user is already at the bottom; scrolling up opts out.
  const stickToBottom = useRef(true)

  const onScroll = () => {
    const list = listRef.current
    if (list) stickToBottom.current = list.scrollHeight - list.scrollTop - list.clientHeight < 48
  }

  useEffect(() => {
    const list = listRef.current
    if (list && stickToBottom.current) list.scrollTop = list.scrollHeight
  }, [messages])

  const send = (value = question) => {
    if (!value.trim() || busy) return
    setQuestion('')
    stickToBottom.current = true
    onSend(value)
  }

  const compose = (value: string) => {
    setQuestion(value)
    textareaRef.current?.focus()
  }

  return <div className="v2-domain-page v2-copilot-page">
    <section className="v2-page-head"><div><span className="v2-eyebrow">{ENGINE_LABELS[engine].source}</span><h1>Hydro Intelligence</h1><p>Ask grounded questions across facilities, equipment, signals, and operational work.</p></div><Bot size={28} /></section>
    <section className={`v2-copilot${conversationEvents.length ? ' has-conversation-crew' : ''}${flowExpanded && conversationEvents.length ? ' has-expanded-flow' : ''}${chatView.expanded ? ' v2-expanded-view' : ''}`}
      style={{ '--flow-width': `${flowWidth}px` } as React.CSSProperties}><header><span><Bot size={17} /><strong>Hydro Operations</strong><small>{ENGINE_LABELS[engine].source}</small></span>
      <span className="v2-copilot-actions">
        {foundryAvailable && (onEngineChange || battleEnabled) && <span className="v2-engine-toggle" role="group" aria-label="Copilot options">
          {onEngineChange && (['data-agent', 'foundry'] as CopilotEngine[]).map(option => <button
            key={option}
            type="button"
            className={option === engine ? 'on' : ''}
            aria-pressed={option === engine}
            disabled={busy}
            onClick={() => onEngineChange(option)}
          >{ENGINE_LABELS[option].name}</button>)}
          {battleEnabled && <button
            type="button"
            title="Compare Fabric Data Agent and Foundry with the same prompt"
            disabled={busy}
            onClick={onBattle}
          >Battle</button>}
        </span>}
        <button className="v2-icon-action" type="button" title={chatView.expanded ? 'Restore chat' : 'Maximize chat'} aria-label={chatView.expanded ? 'Restore chat' : 'Maximize chat'} aria-pressed={chatView.expanded} onClick={chatView.toggleExpanded}>{chatView.expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
        <button className="v2-icon-action" type="button" title="New chat" disabled={busy || messages.length === 1} onClick={() => { setQuestion(''); onReset() }}><SquarePen size={16} /></button>
      </span></header>
      {conversationEvents.length > 0 && <aside className="v2-conversation-crew" aria-label="Conversation agent flow">
        {flowExpanded && <label className="v2-flow-resize">Panel width<input aria-label="Agent panel width" type="range" min="280" max="420" step="20" value={flowWidth} onChange={event => setFlowWidth(Number(event.target.value))} /></label>}
        <AgentCrewTrace events={conversationEvents} proposals={conversationProposals} currentEventIds={currentEventIds} pending={busy} expanded={flowExpanded} onExpandedChange={setFlowExpanded} />
      </aside>}
      <div className="v2-messages" ref={listRef} onScroll={onScroll}>
        {messages.map((message, index) => {
          const last = index === messages.length - 1
          return <div className={`v2-message ${message.role}`} key={index} aria-busy={message.role === 'agent' && busy && last}>
            {message.role === 'agent'
              ? <CopilotResponse message={message} streaming={busy && last} showCrew={false} question={messages[index - 1]?.role === 'user' ? messages[index - 1].text : undefined} />
              : <p>{message.text}</p>}
            {message.role === 'agent' && last && !busy && index > 0 && <SuggestionChips message={message} onCompose={compose} onSend={send} />}
          </div>
        })}
        {messages.length === 1 && <div className="v2-suggestions">{prompts.map(prompt => <button type="button" key={prompt} onClick={() => send(prompt)}>{prompt}</button>)}</div>}
      </div>
      <footer><textarea ref={textareaRef} value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} placeholder="Ask about connected Fabric data" />
        <VoiceInput key={messages.length} disabled={busy} resetKey={messages.length} onTranscript={text => setQuestion(current => `${current.trimEnd()} ${text}`.trimStart())} />
        <button type="button" title="Send" disabled={busy || !question.trim()} onClick={() => send()}><Send size={17} /></button></footer>
    </section>
  </div>
}

function SuggestionChips({ message, onCompose, onSend }: { message: CopilotMessage; onCompose: (value: string) => void; onSend: (value: string) => void }) {
  const suggestions = useMemo(() => relatedSuggestions(message.text, message.proposals?.map(proposal => proposal.equipmentId)), [message.text, message.proposals])
  if (!suggestions.length) return null
  return <div className="v2-suggest-chips">{suggestions.map(suggestion => <span className="v2-suggest-chip" key={suggestion} title={suggestion}>
    <em>{suggestionLabel(suggestion)}</em>
    <button type="button" title="Put in the message box" aria-label={`Edit before sending: ${suggestion}`} onClick={() => onCompose(suggestion)}><SquarePen size={11} /></button>
    <button type="button" title="Send now" aria-label={`Send: ${suggestion}`} onClick={() => onSend(suggestion)}><Send size={11} /></button>
  </span>)}</div>
}

export const CopilotResponse = memo(function CopilotResponse({ message, streaming, question, showCrew = true }: { message: CopilotMessage; streaming: boolean; question?: string; showCrew?: boolean }) {
  return <>
    <AgentMessage message={message} streaming={streaming} question={question} showCrew={showCrew} />
    {message.meta && <MessageFooter message={message} question={question} />}
  </>
})

function AgentMessage({ message, streaming, question, showCrew }: { message: CopilotMessage; streaming: boolean; question?: string; showCrew: boolean }) {
  const receiptPrefix = useId()
  const hasBody = Boolean(message.text || message.artifacts?.length || message.visualizations?.length || message.models?.length || message.orchestrationEvents?.length)
  const steps = message.steps ?? []
  if (!hasBody && !steps.length) return <CopilotThinking />
  // A running tool already shows its own progress, so only flag the gap where the model itself
  // is working and nothing is being echoed yet.
  const waitingOnModel = streaming && !steps.some(step => step.status === 'running')
  const draftReady = !streaming && Boolean(message.proposals?.length)
  const checkedText = message.orchestrationEvents?.some(event => event.trace?.some(entry =>
    entry.source === 'application' && entry.activity === 'checked-presentation' && !entry.failed)) ?? false
  const answer = message.text && (message.backendOwned
    ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
    : <AnswerText text={stripOptionsMarker(streaming ? '' : operatorNarrative(message.text))} streaming={streaming} />)
  return <>
    {showCrew && <AgentCrewTrace events={message.orchestrationEvents} proposals={message.proposals} />}
    {draftReady && message.proposals?.map(proposal => <WorkOrderApprovalCard key={proposal.id} proposal={proposal} />)}
    {streaming ? <CopilotSteps steps={message.steps} receiptPrefix={receiptPrefix} />
      : steps.length > 0 && <details className="v2-answer-support"><summary>Execution audit — tool calls and receipts</summary><CopilotSteps steps={message.steps} receiptPrefix={receiptPrefix} /></details>}
    {waitingOnModel && <p className="v2-agent-processing" role="status" aria-live="polite">
      <span className="v2-spinner" aria-hidden="true" />AI processing…
    </p>}
    {draftReady ? <details className="v2-answer-support"><summary>Supporting findings and sources</summary>{answer}</details> : answer}
    {message.artifacts?.map(artifact => artifact.kind === 'image' && artifact.url
      ? <img className="v2-agent-image" src={artifact.url} alt={artifact.name} key={artifact.fileId} />
      : <a className="v2-agent-file" href={artifact.url} download={artifact.name} aria-disabled={!artifact.url} key={artifact.fileId}><Download size={14} />{artifact.name}</a>)}
    {!streaming && !message.backendOwned && <AnswerDashboard text={message.text} question={question} visualizations={message.visualizations} steps={message.steps} receiptPrefix={receiptPrefix} checkedText={checkedText} />}
    {!streaming && message.backendOwned && message.visualizations?.map((spec, index) => <AgentVisualizationView spec={spec} key={index} />)}
    {!streaming && !message.backendOwned && message.text && <details className="v2-answer-support"><summary>Assessment audit — full report and source references</summary>
      <AnswerText text={hideRenderedData(message.text)} streaming={false} />
    </details>}
    {message.models?.map(model => <AgentModel key={`${model.id}-${model.modelUrl}`} model={model} />)}
    {streaming && message.text && <CopilotStreamCursor />}
  </>
}

function AnswerText({ text, streaming }: { text: string; streaming: boolean }) {
  if (streaming) return <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
  return answerSections(text).map((section, index) => section.collapsed
    ? <details className="v2-answer-support" key={index}><summary>{section.title}</summary><ReactMarkdown remarkPlugins={[remarkGfm]}>{section.markdown.replace(/^#{1,6}\s+.*\n?/, '')}</ReactMarkdown></details>
    : <ReactMarkdown remarkPlugins={[remarkGfm]} key={index}>{section.markdown}</ReactMarkdown>)
}

function AgentModel({ model }: { model: Asset3DModelRecord }) {
  return <figure className="v2-agent-model">
    <figcaption><Box size={14} /><strong>{model.modelName}</strong><small>{model.equipmentId} · {model.format}{model.version ? ` · ${model.version}` : ''}{model.fileSizeMb ? ` · ${model.fileSizeMb} MB` : ''}</small></figcaption>
    {canRenderModel(model.format)
      ? <Suspense fallback={<div className="v2-agent-model-loading">Loading 3D model…</div>}>
        <AssetModelViewer key={model.modelUrl} model={model} signals={[]} assetLabel={model.equipmentId} />
      </Suspense>
      : model.thumbnailUrl
        ? <img src={model.thumbnailUrl} alt={model.modelName} />
        : <div className="v2-agent-model-loading">{model.format} cannot be rendered inline.</div>}
    <a href={model.modelUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open model</a>
  </figure>
}

function prettyJson(raw: string): string {
  try { return JSON.stringify(JSON.parse(raw), null, 2) } catch { return raw }
}

/** The whole exchange as markdown: question, every tool call with its result, then the answer. */
function buildTranscript(question: string | undefined, message: CopilotMessage): string {
  const block = (language: string, body: string) => `\`\`\`${language}\n${body}\n\`\`\``
  const parts: string[] = []
  if (question) parts.push(`## Question\n\n${question}`)
  for (const step of message.steps ?? []) {
    const lines = [`### Tool: ${step.tool}${step.detail ? ` — ${step.detail}` : ''}`]
    lines.push(`${step.error ? `failed: ${step.error}` : step.summary} · ${step.timingSource === 'notification' ? 'status received (not server execution time)' : formatDuration(step.elapsedMs)}`)
    if (step.args) lines.push(`Arguments:\n\n${block('json', prettyJson(step.args))}`)
    if (step.query) lines.push(`Query:\n\n${block('kusto', step.query)}`)
    if (step.result) lines.push(`Result:\n\n${block('json', prettyJson(step.result))}`)
    parts.push(lines.join('\n\n'))
  }
  if (message.orchestrationEvents?.length) {
    parts.push(`## Agent flow\n\n${message.orchestrationEvents.map(event => `- ${event.label}: ${event.status} — ${event.detail}`).join('\n')}`)
  }
  if (message.text) parts.push(`## Answer\n\n${stripOptionsMarker(message.text)}`)
  for (const visualization of message.visualizations ?? []) {
    parts.push(`### Chart: ${visualization.title} (${visualization.chartType})\n\n${block('csv', visualization.inlineCsvData)}`)
  }
  for (const model of message.models ?? []) {
    parts.push(`### 3D model: ${model.modelName}\n\n${model.equipmentId} · ${model.format}\n${model.modelUrl}`)
  }
  if (message.meta) {
    parts.push(`---\n\n${formatDuration(message.meta.elapsedMs)}${message.meta.tokens ? ` · ${message.meta.tokens.toLocaleString()} tokens` : ''}`)
  }
  return parts.join('\n\n')
}

function MessageFooter({ message, question }: { message: CopilotMessage; question?: string }) {
  const [copied, setCopied] = useState(false)
  const meta = message.meta

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(buildTranscript(question, message))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch (error) {
      console.warn('Clipboard write was blocked.', error)
    }
  }

  return <div className="v2-message-footer">
    <small>{meta ? `${formatDuration(meta.elapsedMs)}${meta.tokens ? ` · ${meta.tokens.toLocaleString()} tokens` : ''}` : ''}</small>
    <button type="button" className="v2-copy-answer" title="Copy the question, tool calls and answer" onClick={() => void copy()}>
      {copied ? <Check size={12} /> : <Copy size={12} />}{copied ? 'Copied' : 'Copy'}
    </button>
  </div>
}

function CopilotSteps({ steps, receiptPrefix }: { steps?: AgentStep[]; receiptPrefix?: string }) {
  if (!steps?.length) return null
  return <div className="v2-agent-steps">{steps.map((step, index) => <details id={receiptPrefix ? `${receiptPrefix}-source-${index}` : undefined} className={`v2-agent-step ${step.status}`} key={index}>
    <summary>
      <Wrench size={12} />
      <code>{step.tool}</code>
      {step.detail && <em>{step.detail}</em>}
      <span>{step.status === 'running' ? 'running…' : `${step.error ? 'failed' : step.summary}${step.timingSource === 'notification' ? ' · status received' : ` · ${formatDuration(step.elapsedMs)}`}`}</span>
    </summary>
    <div className="v2-agent-step-body">
      {step.timingSource === 'notification' && <p>Fabric reports lifecycle notifications, not server execution durations. Notifications can arrive together near completion.</p>}
      {step.error && <p className="v2-agent-step-error">{step.error}</p>}
      {step.args && <pre>{prettyJson(step.args)}</pre>}
      {step.query && <pre>{step.query}</pre>}
      {!step.error && !step.args && !step.query && <p>No arguments.</p>}
    </div>
  </details>)}</div>
}

function formatDuration(ms: number) { return ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s` }

type ParsedTable = { headers: string[]; rows: string[][] }
type ChartSeries = { valueHeader: string; points: { label: string; value: number }[] }
type ChartKind = 'bar' | 'line' | 'pie'

function parseFirstTable(markdown: string): ParsedTable | null {
  const lines = markdown.split('\n')
  const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
  for (let index = 0; index < lines.length - 1; index++) {
    const separator = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[index + 1])
    if (!/^\s*\|.*\|\s*$/.test(lines[index]) || !separator) continue
    const headers = cells(lines[index])
    const rows: string[][] = []
    for (let row = index + 2; row < lines.length && /^\s*\|.*\|\s*$/.test(lines[row]); row++) rows.push(cells(lines[row]))
    if (headers.length >= 2 && rows.length) return { headers, rows }
  }
  return null
}

const toNumber = (raw: string) => {
  const value = Number.parseFloat(raw.replace(/[^0-9.eE+-]/g, ''))
  return Number.isFinite(value) ? value : Number.NaN
}

function extractSeries(table: ParsedTable): ChartSeries | null {
  const threshold = Math.ceil(table.rows.length / 2)
  const numericScore = (column: number) => table.rows.filter(row => Number.isFinite(toNumber(row[column] ?? ''))).length
  let valueColumn = -1
  for (let column = table.headers.length - 1; column >= 0; column--) {
    if (numericScore(column) >= threshold) { valueColumn = column; break }
  }
  if (valueColumn < 0) return null
  const labelColumn = table.headers.findIndex((_, column) => column !== valueColumn && numericScore(column) < threshold)
  if (labelColumn < 0) return null
  const points = table.rows
    .map(row => ({ label: row[labelColumn] ?? '', value: toNumber(row[valueColumn] ?? '') }))
    .filter(point => point.label && Number.isFinite(point.value))
    .slice(0, 16)
  return points.length ? { valueHeader: table.headers[valueColumn] ?? 'Value', points } : null
}

export function ChartFromMarkdown({ markdown }: { markdown: string }) {
  const series = useMemo(() => { const table = parseFirstTable(markdown); return table ? extractSeries(table) : null }, [markdown])
  const [kind, setKind] = useState<ChartKind>('bar')
  if (!series) return null
  return <div className="v2-agent-chart" aria-label="Visualization generated from the response table">
    <div className="v2-chart-tabs" role="group" aria-label="Chart type">
      <ChartButton active={kind === 'bar'} label="Bar chart" onClick={() => setKind('bar')}><BarChart3 size={14} /></ChartButton>
      <ChartButton active={kind === 'line'} label="Line chart" onClick={() => setKind('line')}><LineChart size={14} /></ChartButton>
      <ChartButton active={kind === 'pie'} label="Pie chart" onClick={() => setKind('pie')}><PieChart size={14} /></ChartButton>
    </div>
    <ChartSvg series={series} kind={kind} />
  </div>
}

function ChartButton({ active, label, onClick, children }: { active: boolean; label: string; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" className={active ? 'on' : ''} title={label} aria-label={label} aria-pressed={active} onClick={onClick}>{children}</button>
}

const CHART_COLORS = ['#2f9e8f', '#4c8bf5', '#f5a623', '#e0554e', '#7b61ff', '#12a150', '#e879a6', '#8a94a6']

function ChartSvg({ series, kind }: { series: ChartSeries; kind: ChartKind }) {
  const width = 420, height = 230, left = 42, right = 16, top = 12, bottom = 58
  const maximum = Math.max(...series.points.map(point => point.value), 0) || 1
  const plotWidth = width - left - right, plotHeight = height - top - bottom
  const shorten = (text: string) => text.length > 12 ? `${text.slice(0, 11)}…` : text
  if (kind === 'pie') {
    const total = series.points.reduce((sum, point) => sum + Math.max(point.value, 0), 0)
    if (!total) return null
    const centerX = 120, centerY = height / 2, radius = 82
    const fractions = series.points.map(point => Math.max(point.value, 0) / total)
    const slices = series.points.map((point, index) => {
      const fraction = fractions[index]
      const start = -Math.PI / 2 + fractions.slice(0, index).reduce((sum, value) => sum + value, 0) * Math.PI * 2
      const end = start + fraction * Math.PI * 2
      const position = (value: number) => `${centerX + radius * Math.cos(value)} ${centerY + radius * Math.sin(value)}`
      const path = fraction >= .9999 ? `M ${centerX - radius} ${centerY} A ${radius} ${radius} 0 1 1 ${centerX + radius} ${centerY} A ${radius} ${radius} 0 1 1 ${centerX - radius} ${centerY} Z` : `M ${centerX} ${centerY} L ${position(start)} A ${radius} ${radius} 0 ${end - start > Math.PI ? 1 : 0} 1 ${position(end)} Z`
      return { point, fraction, path, color: CHART_COLORS[index % CHART_COLORS.length] }
    })
    return <svg viewBox={`0 0 ${width} ${height}`} className="v2-chart-svg" role="img" aria-label={`Pie chart of ${series.valueHeader}`}>
      {slices.map(slice => <path key={slice.point.label} d={slice.path} fill={slice.color} stroke="var(--cp-surface)" />)}
      {slices.map((slice, index) => <g key={`legend-${slice.point.label}`} transform={`translate(235 ${top + index * 20})`}><rect width="10" height="10" rx="2" fill={slice.color} /><text x="16" y="9" className="v2-chart-label">{shorten(slice.point.label)} {Math.round(slice.fraction * 100)}%</text></g>)}
    </svg>
  }
  const count = series.points.length
  const x = (index: number) => left + (count === 1 ? plotWidth / 2 : index * plotWidth / (count - 1))
  const y = (value: number) => top + plotHeight - value / maximum * plotHeight
  return <svg viewBox={`0 0 ${width} ${height}`} className="v2-chart-svg" role="img" aria-label={`${kind} chart of ${series.valueHeader}`}>
    <line x1={left} y1={top} x2={left} y2={top + plotHeight} className="v2-chart-axis" /><line x1={left} y1={top + plotHeight} x2={width - right} y2={top + plotHeight} className="v2-chart-axis" />
    <text x={left - 7} y={top + 4} textAnchor="end" className="v2-chart-label">{maximum.toLocaleString()}</text><text x={left - 7} y={top + plotHeight} textAnchor="end" className="v2-chart-label">0</text>
    {kind === 'bar' && series.points.map((point, index) => { const barWidth = Math.max(7, Math.min(34, plotWidth / count * .62)); const center = left + plotWidth / count * (index + .5); const barHeight = point.value / maximum * plotHeight; return <rect key={point.label} x={center - barWidth / 2} y={top + plotHeight - barHeight} width={barWidth} height={barHeight} rx="3" fill={CHART_COLORS[index % CHART_COLORS.length]} /> })}
    {kind === 'line' && <><polyline points={series.points.map((point, index) => `${x(index)},${y(point.value)}`).join(' ')} className="v2-chart-line" />{series.points.map((point, index) => <circle key={point.label} cx={x(index)} cy={y(point.value)} r="3" fill={CHART_COLORS[0]} />)}</>}
    {series.points.map((point, index) => { const center = kind === 'bar' ? left + plotWidth / count * (index + .5) : x(index); return <text key={`label-${point.label}`} x={center} y={top + plotHeight + 15} textAnchor="end" transform={`rotate(-35 ${center} ${top + plotHeight + 15})`} className="v2-chart-label">{shorten(point.label)}</text> })}
    <text x={left + plotWidth / 2} y={height - 4} textAnchor="middle" className="v2-chart-title">{series.valueHeader}</text>
  </svg>
}
