import { Sparkles } from 'lucide-react'

export function CopilotThinking() {
  return <span className="copilot-thinking" role="status" aria-live="polite">
    <Sparkles size={14} aria-hidden="true" />
    <span className="copilot-thinking-label">Thinking</span>
    <span className="copilot-thinking-dots" aria-hidden="true"><i /><i /><i /></span>
  </span>
}

export function CopilotStreamCursor() {
  return <span className="copilot-stream-cursor" aria-hidden="true" />
}