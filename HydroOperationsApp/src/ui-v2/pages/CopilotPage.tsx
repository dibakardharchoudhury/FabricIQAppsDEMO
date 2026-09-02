import { useMemo, useState } from 'react'
import { Bot, Send, SquarePen } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FacilityContext } from '../components/FacilityContext'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function CopilotPage() {
  const data = useHydroOperationsData()
  const [question, setQuestion] = useState('')
  const prompts = useMemo(() => [
    'Show all open work orders as a table with the affected asset, priority, and status.',
    data.selectedFacility ? `List the equipment at ${data.selectedFacility.facility_id} with manufacturer, model, and criticality.` : 'List all equipment with manufacturer, model, and criticality.',
    'Which assets have the most open work orders? Give a ranked table and chart.',
    'Summarize the facilities with their type, country, and number of assets.',
  ], [data.selectedFacility])
  const send = (value = question) => {
    if (!value.trim() || data.copilotBusy) return
    setQuestion('')
    void data.actions.sendCopilotQuestion(value)
  }
  return <div className="v2-domain-page">
    <FacilityContext />
    <section className="v2-page-head"><div><span className="v2-eyebrow">Fabric Data Agent</span><h1>Operations Copilot</h1><p>Ask grounded questions across facilities, equipment, signals, and operational work.</p></div><Bot size={28} /></section>
    <section className="v2-copilot"><header><span><Bot size={17} /><strong>Hydro Operations</strong><small>Connected Fabric data</small></span><button className="v2-icon-action" type="button" title="New chat" disabled={data.copilotBusy || data.messages.length === 1} onClick={data.actions.resetCopilot}><SquarePen size={16} /></button></header>
      <div className="v2-messages">{data.messages.map((message, index) => <div className={`v2-message ${message.role}`} key={index}>{message.role === 'agent' ? message.text ? <><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>{message.chart && <ChartFromMarkdown markdown={message.text} />}</> : <span className="v2-thinking">Thinking<span>...</span></span> : <p>{message.text}</p>}{message.meta && <small>{formatDuration(message.meta.elapsedMs)}{message.meta.tokens ? ` · ${message.meta.tokens.toLocaleString()} tokens` : ''}</small>}</div>)}{data.messages.length === 1 && <div className="v2-suggestions">{prompts.map(prompt => <button type="button" key={prompt} onClick={() => send(prompt)}>{prompt}</button>)}</div>}</div>
      <footer><textarea value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} placeholder="Ask about connected Fabric data" /><button type="button" title="Send" disabled={data.copilotBusy || !question.trim()} onClick={() => send()}><Send size={17} /></button></footer>
    </section>
  </div>
}

function formatDuration(ms: number) { return ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s` }

function ChartFromMarkdown({ markdown }: { markdown: string }) {
  const points = useMemo(() => {
    const lines = markdown.split('\n')
    const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
    const header = lines.findIndex((line, index) => /^\s*\|.*\|\s*$/.test(line) && lines[index + 1]?.includes('-'))
    if (header < 0) return []
    const parsed: { label: string; value: number }[] = []
    for (let index = header + 2; index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index]); index++) {
      const row = cells(lines[index])
      const numeric = row.map(value => Number.parseFloat(value.replace(/[^0-9.eE+-]/g, '')))
      const valueIndex = numeric.findLastIndex(Number.isFinite)
      if (valueIndex >= 0) parsed.push({ label: row.find((_, cellIndex) => cellIndex !== valueIndex) ?? '', value: numeric[valueIndex] })
    }
    return parsed.slice(0, 12)
  }, [markdown])
  if (!points.length) return null
  const max = Math.max(...points.map(point => point.value), 1)
  return <div className="v2-agent-chart" aria-label="Chart generated from the response table">{points.map(point => <div key={point.label}><span>{point.label}</span><i><b style={{ width: `${Math.max(2, point.value / max * 100)}%` }} /></i><strong>{point.value}</strong></div>)}</div>
}