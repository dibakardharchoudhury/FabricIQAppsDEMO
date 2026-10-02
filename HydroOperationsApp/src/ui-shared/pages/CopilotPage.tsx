import { useEffect } from 'react'
import { CopilotExperience } from '../../components/CopilotExperience'
import { warmDataAgentMcp } from '../../services/fabric'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function CopilotPage() {
  const data = useHydroOperationsData()
  useEffect(() => {
    if (data.copilotEngine === 'data-agent') void warmDataAgentMcp().catch(error => console.warn('Data Agent MCP warm-up failed.', error))
  }, [data.copilotEngine])
  return <CopilotExperience
    messages={data.messages}
    busy={data.copilotBusy}
    engine={data.copilotEngine}
    foundryAvailable={data.foundryAvailable}
    onSend={question => void data.actions.sendCopilotQuestion(question)}
    onReset={data.actions.resetCopilot}
    onEngineChange={data.actions.setCopilotEngine}
  />
}