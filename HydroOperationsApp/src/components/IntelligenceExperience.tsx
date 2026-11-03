import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Bot, ChevronDown, Layers3 } from 'lucide-react'
import { CopilotExperience, CopilotResponse } from './CopilotExperience'
import { INTELLIGENCE_MODES, resolveVisibleIntelligenceMode, visibleIntelligenceModes, type IntelligenceConversations, type IntelligenceMode } from '../services/intelligenceModes'
import { loadCopilotSettings, subscribeCopilotSettings } from '../services/copilot/settings'

export function IntelligenceExperience({ conversations, snapshot, identity }: {
  conversations: IntelligenceConversations
  snapshot: ReturnType<IntelligenceConversations['getSnapshot']>
  identity: string
}) {
  const [mode, setMode] = useState<IntelligenceMode>('multi')
  const [visibility, setVisibility] = useState(() => loadCopilotSettings().intelligenceModeVisibility)
  const id = useId()
  const picker = useRef<HTMLElement | null>(null)
  const visibleModes = visibleIntelligenceModes(visibility)
  const selectedMode = resolveVisibleIntelligenceMode(mode, visibility)
  const selected = INTELLIGENCE_MODES.find(tab => tab.id === selectedMode)
  useEffect(() => subscribeCopilotSettings(() => setVisibility(loadCopilotSettings().intelligenceModeVisibility)), [])
  useEffect(() => {
    for (const tab of INTELLIGENCE_MODES) if (!visibility[tab.id]) conversations.cancel(tab.id)
  }, [conversations, visibility])
  const battleBusy = snapshot.battleDirect.busy || snapshot.battleMulti.busy
  const modeControls = selected && visibleModes.length > 1 ? <details className="v2-intelligence-mode-picker"
    onKeyDown={event => { if (event.key === 'Escape') { event.currentTarget.open = false; picker.current?.focus() } }}>
    <summary ref={picker} aria-label={`Chat mode: ${selected.label}`} title="Choose chat mode">
      <Layers3 size={14} /><span>{selected.id === 'battle' ? 'Battle' : selected.label}</span><ChevronDown size={12} />
    </summary>
    <fieldset>
      <legend>Chat mode</legend>
      {visibleModes.map(tab => <label key={tab.id}>
        <input type="radio" name={`${id}-mode`} checked={selectedMode === tab.id}
          aria-controls={`${id}-${tab.id}-panel`} onChange={event => {
            event.currentTarget.closest('details')?.removeAttribute('open')
            setMode(tab.id)
            requestAnimationFrame(() => picker.current?.focus())
          }} />
        <span>{tab.label}</span>
      </label>)}
    </fieldset>
  </details> : undefined
  return <div className="v2-domain-page v2-intelligence-modes">
    <section className="v2-page-head"><div><h1>Hydro Intelligence</h1><p>Direct Fabric answers, governed collaboration, or a read-only comparison.</p></div></section>
    {!selectedMode && <p className="v2-direct-limit" role="status">Chat modes are hidden. Enable them in Administration under Chat mode visibility.</p>}
    {INTELLIGENCE_MODES.map(tab => <section key={tab.id} role="region" aria-label={`${tab.label} conversation`}
      id={`${id}-${tab.id}-panel`}
      hidden={selectedMode !== tab.id} style={selectedMode !== tab.id ? { display: 'none' } : undefined}>
      {tab.id === 'battle'
        ? <BattlePanel key={identity} conversations={conversations} snapshot={snapshot} busy={battleBusy}
          headerControls={selectedMode === tab.id ? modeControls : undefined} />
        : <CopilotExperience key={identity} embedded direct={tab.id === 'direct'}
          headerControls={selectedMode === tab.id ? modeControls : undefined}
          label={tab.id === 'direct' ? 'Fabric IQ · Direct published Data Agent' : 'Supervisor-led multi-agent team'}
          messages={snapshot[tab.id].messages} busy={snapshot[tab.id].busy}
          onSend={question => { void conversations.send(tab.id, question) }}
          onReset={() => conversations.reset(tab.id)} onCancel={() => conversations.cancel(tab.id)} />}
    </section>)}
  </div>
}

function BattlePanel({ conversations, snapshot, busy, headerControls }: {
  conversations: IntelligenceConversations
  snapshot: ReturnType<IntelligenceConversations['getSnapshot']>
  busy: boolean
  headerControls?: ReactNode
}) {
  const [question, setQuestion] = useState('')
  const send = () => {
    if (!question.trim() || busy) return
    void conversations.send('battle', question)
    setQuestion('')
  }
  return <div className="v2-battle-panel">
    <header className="v2-battle-header"><span><Bot size={17} /><strong>Battle of the Agents</strong></span><span>{headerControls}</span></header>
    <p>Identical question, independent sessions. Direct Fabric IQ questions do not inherit earlier context; Multi Agent maintains its own conversation. Durations include authentication, source verification and retrieval. No judge or automatic winner. Review and approval are available only in Multi Agent.</p>
    <div className="v2-battle-columns">
      {(['battleDirect', 'battleMulti'] as const).map(lane => <section key={lane} aria-label={lane === 'battleDirect' ? 'Fabric IQ comparison answer' : 'Multi Agent comparison answer'}>
        <h2>{lane === 'battleDirect' ? 'Fabric IQ — Direct' : 'Multi Agent — Foundry'}</h2>
        {snapshot[lane].messages.map((message, index) => <div key={index} className={`v2-message ${message.role}`} aria-busy={snapshot[lane].busy && index === snapshot[lane].messages.length - 1}>
          {message.role === 'user' ? <p>{message.text}</p> : <CopilotResponse message={{ ...message, readOnly: true }} streaming={snapshot[lane].busy && index === snapshot[lane].messages.length - 1}
            question={snapshot[lane].messages[index - 1]?.text} />}
        </div>)}
      </section>)}
    </div>
    <div className="v2-battle-compose">
      <textarea aria-label="Battle question" placeholder="Ask both modes the same question" value={question}
        onChange={event => setQuestion(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} />
      <button type="button" disabled={busy || !question.trim()} onClick={send}>Compare answers</button>
      {busy && <button type="button" onClick={() => conversations.cancel('battle')}>Cancel comparison</button>}
      <button type="button" disabled={busy} onClick={() => { setQuestion(''); conversations.reset('battle') }}>New comparison</button>
    </div>
  </div>
}
