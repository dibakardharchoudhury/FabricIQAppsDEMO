export type AdministrationStep = {
  n: number
  title: string
  why: string
  done: boolean
  busy: boolean
  action: string
  run: () => void
}

type AdministrationExperienceProps = {
  steps: AdministrationStep[]
}

export function AdministrationExperience({ steps }: AdministrationExperienceProps) {
  const complete = steps.every(step => step.done)

  return <section className="setup">
    <div className="setup-head">
      <span className="eyebrow">GUIDED SETUP</span>
      <p>{complete ? 'App data setup steps are complete. Agent execution and alert delivery are not verified here. Use the actions below to verify each data connection.' : 'Steps 2 and 3 are independent — you can start them together, then finish 4 and 5.'}</p>
      <p>Agents are enabled by default; auto also attempts provisioning, and only disabled opts out. Required agent failures fail provisioning. In the shared notebook configuration, check <code>data_agent_deployment_status</code>/<code>data_agent_deployment_reason</code> and <code>ops_agent_deployment_status</code>/<code>ops_agent_deployment_reason</code>. These app data checks do not verify playbook execution or Teams/email delivery; configuration and runtime readiness must be checked separately.</p>
    </div>
    <ol className="setup-steps">{steps.map(step => <li key={step.n} className={step.done ? 'setup-step done' : 'setup-step'}>
      <span className="step-num" aria-label={`Step ${step.n}`}>{step.n}</span>
      <div className="step-body"><strong>{step.title}</strong><small>{step.why}</small></div>
      <button className="step-action" onClick={step.run} disabled={step.busy || step.done}>{step.done ? 'Done' : step.busy ? 'Working…' : step.action}</button>
    </li>)}</ol>
  </section>
}
