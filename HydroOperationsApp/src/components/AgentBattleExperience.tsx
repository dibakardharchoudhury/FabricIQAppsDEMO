import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, Gauge, RotateCcw, Send, Swords } from 'lucide-react'
import { resetDataAgentConversation, type AgentAnswer } from '../services/fabric'
import { askCopilot } from '../services/copilot/askCopilot'
import { isWorkOrderRequest } from '../services/copilot/orchestration'
import { askFoundryCopilot, resetFoundryConversation, type FoundryAnswer } from '../services/copilot/foundry'
import { runAgentBattle, type AgentBattleEngine, type AgentBattleMode } from '../services/copilot/agentBattle'
import { CopilotResponse, type CopilotMessage } from './CopilotExperience'
import { AgentElapsedTime } from './AgentElapsedTime'
import { VoiceInput } from './VoiceInput'

type BattleSide = {
  status: 'idle' | 'queued' | 'running' | 'completed' | 'error'
  startedAt?: number
  message?: CopilotMessage
}

type BattleSides = Record<AgentBattleEngine, BattleSide>

const EMPTY_SIDES: BattleSides = {
  'data-agent': { status: 'idle' },
  foundry: { status: 'idle' },
}

const LABELS: Record<AgentBattleEngine, { name: string; source: string }> = {
  'data-agent': { name: 'Fabric Data Agent', source: 'Published MCP · Preview Runtime' },
  foundry: { name: 'Foundry Agents', source: 'Persistent supervisor + specialists' },
}

const SAMPLES = [
  'Summarize each facility with its number of assets and open work orders.',
  'Which assets have the most open work orders? Give a ranked table and chart.',
  'Chart the last 6 hours of telemetry for every signal on T005.',
]

const asMessage = (answer: AgentAnswer | FoundryAnswer, elapsedMs: number): CopilotMessage => ({
  role: 'agent',
  text: answer.text,
  steps: 'steps' in answer ? answer.steps : undefined,
  artifacts: answer.artifacts,
  visualizations: answer.visualizations,
  models: 'models' in answer ? answer.models : undefined,
  orchestrationEvents: 'orchestrationEvents' in answer ? answer.orchestrationEvents : undefined,
  proposals: 'proposals' in answer ? answer.proposals : undefined,
  meta: { elapsedMs, tokens: answer.usage?.total },
})

export function AgentBattleExperience({ onExit }: { onExit: () => void }) {
  const [prompt, setPrompt] = useState('')
  const [lastPrompt, setLastPrompt] = useState('')
  const [voiceReset, setVoiceReset] = useState(0)
  const [mode, setMode] = useState<AgentBattleMode>('sequential')
  const [sides, setSides] = useState<BattleSides>(EMPTY_SIDES)
  const running = Object.values(sides).some(side => side.status === 'running' || side.status === 'queued')
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    resetDataAgentConversation()
    resetFoundryConversation()
  }, [])

  const updateSide = (engine: AgentBattleEngine, patch: Partial<BattleSide>) =>
    setSides(current => ({ ...current, [engine]: { ...current[engine], ...patch } }))

  const updateMessage = (engine: AgentBattleEngine, patch: Partial<CopilotMessage>) =>
    setSides(current => ({
      ...current,
      [engine]: {
        ...current[engine],
        message: { role: 'agent', ...current[engine].message, ...patch },
      },
    }))

  const run = async () => {
    const exactPrompt = prompt.trim()
    if (!exactPrompt || running) return
    const executionMode = isWorkOrderRequest(exactPrompt) ? 'sequential' : mode
    setPrompt('')
    setLastPrompt(exactPrompt)
    setSides({
      'data-agent': { status: 'running', message: { role: 'agent', text: '' } },
      foundry: { status: executionMode === 'parallel' ? 'running' : 'queued', message: { role: 'agent', text: '' } },
    })

    const runners = {
      'data-agent': async (question: string) => {
        updateSide('data-agent', { status: 'running', startedAt: Date.now() })
        return askCopilot(
          'data-agent',
          question,
          text => updateMessage('data-agent', { text }),
          steps => updateMessage('data-agent', { steps }),
          orchestrationEvents => updateMessage('data-agent', { orchestrationEvents }),
        )
      },
      foundry: async (question: string) => {
        updateSide('foundry', { status: 'running', startedAt: Date.now() })
        return askFoundryCopilot(
          question,
          text => updateMessage('foundry', { text }),
          steps => updateMessage('foundry', { steps }),
          orchestrationEvents => updateMessage('foundry', { orchestrationEvents }),
        )
      },
    }

    await runAgentBattle<AgentAnswer | FoundryAnswer>(exactPrompt, executionMode, runners, result => {
      if (result.ok && result.value) {
        updateSide(result.engine, { status: 'completed', message: asMessage(result.value, result.elapsedMs) })
      } else {
        updateMessage(result.engine, {
          text: result.error ?? 'The agent request failed.',
          meta: { elapsedMs: result.elapsedMs },
        })
        updateSide(result.engine, { status: 'error' })
      }
    })
  }

  const reset = () => {
    if (running) return
    resetDataAgentConversation()
    resetFoundryConversation()
    setSides(EMPTY_SIDES)
    setLastPrompt('')
    setPrompt('')
    setVoiceReset(value => value + 1)
    inputRef.current?.focus()
  }

  const fastest = useMemo(() => {
    const dataTime = sides['data-agent'].status === 'completed' ? sides['data-agent'].message?.meta?.elapsedMs : undefined
    const foundryTime = sides.foundry.status === 'completed' ? sides.foundry.message?.meta?.elapsedMs : undefined
    if (dataTime === undefined || foundryTime === undefined) return undefined
    return dataTime <= foundryTime ? 'data-agent' : 'foundry'
  }, [sides])

  return <div className="v2-domain-page v2-copilot-page agent-battle-page">
    <section className="v2-page-head">
      <div><span className="v2-eyebrow">Controlled agent comparison</span><h1>Battle of the Agents</h1><p>The exact same prompt is sent to both configured runtimes. Timing is objective; review correctness and quality yourself.</p></div>
      <Swords size={28} />
    </section>

    <section className="agent-battle-toolbar">
      <div className="v2-engine-toggle" role="group" aria-label="Battle execution mode">
        <button type="button" className={mode === 'sequential' ? 'on' : ''} disabled={running} onClick={() => setMode('sequential')}>Capacity-safe</button>
        <button type="button" className={mode === 'parallel' ? 'on' : ''} disabled={running} onClick={() => setMode('parallel')}>Parallel</button>
      </div>
      <small>{mode === 'sequential' ? 'Runs one agent at a time so shared Fabric capacity does not distort results.' : 'Runs both together for wall-clock speed; shared capacity contention can affect latency.'}</small>
      <span>
        <button type="button" disabled={running} onClick={reset}><RotateCcw size={14} />Reset</button>
        <button type="button" disabled={running} onClick={onExit}>Exit battle</button>
      </span>
    </section>

    {!lastPrompt && <div className="agent-battle-samples">{SAMPLES.map(sample => <button type="button" key={sample} onClick={() => { setPrompt(sample); inputRef.current?.focus() }}>{sample}</button>)}</div>}
    {lastPrompt && <div className="agent-battle-prompt"><strong>Prompt</strong><span>{lastPrompt}</span></div>}
    {isWorkOrderRequest(lastPrompt) && <p>Work-order requests use the Foundry approval flow on both sides, sequentially. This is not a comparison of two independent read-only engines.</p>}

    <div className="agent-battle-grid">
      {(['data-agent', 'foundry'] as AgentBattleEngine[]).map(engine => {
        const side = sides[engine]
        const label = LABELS[engine]
        const active = side.status === 'running'
        return <section className={`agent-battle-pane ${fastest === engine ? 'fastest' : ''}`} key={engine}>
          <header>
            <span><Bot size={17} /><strong>{label.name}</strong><small>{label.source}</small></span>
            <AgentElapsedTime startedAt={side.startedAt} elapsedMs={side.message?.meta?.elapsedMs} running={active} />
            <em className={`agent-battle-status ${side.status}`}>{fastest === engine ? 'fastest' : side.status}</em>
          </header>
          <div className="agent-battle-body">
            {side.status === 'queued'
              ? <p className="agent-battle-empty"><Gauge size={20} />Waiting for the capacity-safe Data Agent run to finish.</p>
              : side.message
              ? <div className="v2-message agent" aria-busy={active}><CopilotResponse message={side.message} streaming={active} question={lastPrompt} /></div>
              : <p className="agent-battle-empty"><Gauge size={20} />Ready for the same prompt.</p>}
          </div>
        </section>
      })}
    </div>

    <footer className="agent-battle-composer">
      <textarea ref={inputRef} value={prompt} onChange={event => setPrompt(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void run() } }} placeholder="Ask both agents the same question" />
      <VoiceInput disabled={running} resetKey={voiceReset} onTranscript={text => setPrompt(current => `${current.trimEnd()} ${text}`.trimStart())} />
      <button type="button" disabled={running || !prompt.trim()} onClick={() => void run()}><Send size={17} />Ask both</button>
    </footer>
  </div>
}
