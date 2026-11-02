import { useId, useRef, useState } from 'react'
import { CopilotExperience, CopilotResponse } from './CopilotExperience'
import { INTELLIGENCE_MODES, type IntelligenceConversations, type IntelligenceMode } from '../services/intelligenceModes'

export function IntelligenceExperience({ conversations, snapshot, identity }: {
  conversations: IntelligenceConversations
  snapshot: ReturnType<IntelligenceConversations['getSnapshot']>
  identity: string
}) {
  const [mode, setMode] = useState<IntelligenceMode>('multi')
  const id = useId()
  const tabs = useRef<Array<HTMLButtonElement | null>>([])
  const battleBusy = snapshot.battleDirect.busy || snapshot.battleMulti.busy
  return <div className="v2-domain-page v2-intelligence-modes">
    <section className="v2-page-head"><div><h1>Hydro Intelligence</h1><p>Direct Fabric answers, governed collaboration, or a read-only comparison.</p></div></section>
    <div role="tablist" aria-label="Hydro Intelligence modes" className="v2-intelligence-tabs">
      {INTELLIGENCE_MODES.map((tab, index) => <button key={tab.id} type="button" role="tab"
        id={`${id}-${tab.id}-tab`} aria-controls={`${id}-${tab.id}-panel`} aria-selected={mode === tab.id}
        tabIndex={mode === tab.id ? 0 : -1} ref={element => { tabs.current[index] = element }}
        onClick={() => setMode(tab.id)} onKeyDown={event => {
          let next: number
          if (event.key === 'ArrowRight') next = (index + 1) % INTELLIGENCE_MODES.length
          else if (event.key === 'ArrowLeft') next = (index + INTELLIGENCE_MODES.length - 1) % INTELLIGENCE_MODES.length
          else if (event.key === 'Home') next = 0
          else if (event.key === 'End') next = INTELLIGENCE_MODES.length - 1
          else return
          event.preventDefault()
          setMode(INTELLIGENCE_MODES[next].id)
          tabs.current[next]?.focus()
        }}>{tab.label}</button>)}
    </div>
    {INTELLIGENCE_MODES.map(tab => <section key={tab.id} role="tabpanel" tabIndex={0}
      id={`${id}-${tab.id}-panel`} aria-labelledby={`${id}-${tab.id}-tab`}
      hidden={mode !== tab.id} style={mode !== tab.id ? { display: 'none' } : undefined}>
      {tab.id === 'battle'
        ? <BattlePanel key={identity} conversations={conversations} snapshot={snapshot} busy={battleBusy} />
        : <CopilotExperience key={identity} embedded direct={tab.id === 'direct'}
          label={tab.id === 'direct' ? 'Fabric IQ · Direct published Data Agent' : 'Supervisor-led multi-agent team'}
          messages={snapshot[tab.id].messages} busy={snapshot[tab.id].busy}
          onSend={question => { void conversations.send(tab.id, question) }}
          onReset={() => conversations.reset(tab.id)} onCancel={() => conversations.cancel(tab.id)} />}
    </section>)}
  </div>
}

function BattlePanel({ conversations, snapshot, busy }: {
  conversations: IntelligenceConversations
  snapshot: ReturnType<IntelligenceConversations['getSnapshot']>
  busy: boolean
}) {
  const [question, setQuestion] = useState('')
  const send = () => {
    if (!question.trim() || busy) return
    void conversations.send('battle', question)
    setQuestion('')
  }
  return <div className="v2-battle-panel">
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
