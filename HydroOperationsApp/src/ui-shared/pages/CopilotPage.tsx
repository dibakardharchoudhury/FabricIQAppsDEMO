import { lazy, Suspense, useState, useSyncExternalStore } from 'react'
import { CopilotExperience } from '../../components/CopilotExperience'
import { isAgentBattleEnabled, subscribeCopilotSettings } from '../../services/copilot/settings'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

const AgentBattleExperience = lazy(() => import('../../components/AgentBattleExperience').then(module => ({ default: module.AgentBattleExperience })))

export function CopilotPage() {
  const data = useHydroOperationsData()
  const battleEnabled = useSyncExternalStore(subscribeCopilotSettings, isAgentBattleEnabled, isAgentBattleEnabled)
  const [view, setView] = useState<'chat' | 'battle'>('chat')
  const battleActive = battleEnabled && view === 'battle'

  if (battleActive) {
    return <Suspense fallback={<div className="v2-page-loading"><span />Loading agent comparison…</div>}>
      <AgentBattleExperience onExit={() => { data.actions.resetCopilot(); setView('chat') }} />
    </Suspense>
  }

  return <CopilotExperience
    messages={data.messages}
    busy={data.copilotBusy}
    engine={data.copilotEngine}
    foundryAvailable={data.foundryAvailable}
    battleEnabled={battleEnabled}
    onSend={question => void data.actions.sendCopilotQuestion(question)}
    onReset={data.actions.resetCopilot}
    onBattle={() => setView('battle')}
  />
}