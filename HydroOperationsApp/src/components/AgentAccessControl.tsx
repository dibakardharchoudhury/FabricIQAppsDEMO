import { useState } from 'react'
import { connectHostedSourceAccess } from '../services/fabric'

export function AgentAccessControl({ disabled = false }: { disabled?: boolean }) {
  const [connecting, setConnecting] = useState(false)
  const [status, setStatus] = useState<{ text: string; failed: boolean }>()
  const connect = async () => {
    setConnecting(true)
    setStatus(undefined)
    try {
      const remaining = await connectHostedSourceAccess()
      setStatus({ failed: false, text: remaining.length
        ? `Access connected. Click Connect agent access again to authorize: ${remaining.join(', ')}.`
        : 'Agent access is connected. No question or write decision was resubmitted.' })
    } catch (error) {
      console.error('Agent source authorization failed.', error)
      setStatus({ failed: true, text: error instanceof Error ? error.message : 'Agent source authorization failed.' })
    } finally { setConnecting(false) }
  }
  return <span>
    <button type="button" disabled={disabled || connecting} onClick={() => void connect()}>
      {connecting ? 'Connecting access...' : 'Connect agent access'}
    </button>
    {status && <span role={status.failed ? 'alert' : 'status'}>{status.text}</span>}
  </span>
}
