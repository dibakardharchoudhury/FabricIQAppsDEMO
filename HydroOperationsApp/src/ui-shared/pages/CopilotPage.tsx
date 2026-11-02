import { useEffect } from 'react'
import { CopilotExperience } from '../../components/CopilotExperience'
import { warmHostedCopilot } from '../../services/copilot/foundry'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function CopilotPage() {
  const data = useHydroOperationsData()
  const foundryAvailable = data.foundryAvailable

  useEffect(() => {
    if (!foundryAvailable) return
    warmHostedCopilot()
  }, [foundryAvailable])

  return <CopilotExperience
    messages={data.messages}
    busy={data.copilotBusy}
    onSend={question => void data.actions.sendCopilotQuestion(question)}
    onReset={data.actions.resetCopilot}
  />
}