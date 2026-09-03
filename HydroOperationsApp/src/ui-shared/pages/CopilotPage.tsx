import { CopilotExperience } from '../../components/CopilotExperience'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function CopilotPage() {
  const data = useHydroOperationsData()
  return <CopilotExperience
    messages={data.messages}
    busy={data.copilotBusy}
    onSend={question => void data.actions.sendCopilotQuestion(question)}
    onReset={data.actions.resetCopilot}
  />
}