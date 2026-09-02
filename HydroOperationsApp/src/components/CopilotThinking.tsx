export function CopilotThinking() {
  return <span className="copilot-thinking" role="status" aria-live="polite">
    <span className="copilot-helper-stage" aria-hidden="true">
      <span className="copilot-helper">
        <i className="copilot-helper-antenna" />
        <i className="copilot-helper-visor" />
        <i className="copilot-helper-mouth" />
        <i className="copilot-helper-arm left" />
        <i className="copilot-helper-arm right" />
        <i className="copilot-helper-leg left" />
        <i className="copilot-helper-leg right" />
      </span>
      <span className="copilot-helper-thought"><i /><i /><b>?</b></span>
    </span>
    <span className="copilot-thinking-copy">
      <span className="copilot-thinking-label">Thinking hard</span>
      <span className="copilot-thinking-dots" aria-hidden="true"><i /><i /><i /></span>
    </span>
  </span>
}

export function CopilotStreamCursor() {
  return <span className="copilot-stream-cursor" aria-hidden="true" />
}