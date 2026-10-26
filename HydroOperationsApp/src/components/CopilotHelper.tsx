import type { CSSProperties } from 'react'

export function CopilotHelper({ style }: { style?: CSSProperties }) {
  return <span className="copilot-helper" style={style} aria-hidden="true">
    <i className="copilot-helper-antenna" />
    <i className="copilot-helper-visor" />
    <i className="copilot-helper-mouth" />
    <i className="copilot-helper-arm left" />
    <i className="copilot-helper-arm right" />
    <i className="copilot-helper-leg left" />
    <i className="copilot-helper-leg right" />
  </span>
}
