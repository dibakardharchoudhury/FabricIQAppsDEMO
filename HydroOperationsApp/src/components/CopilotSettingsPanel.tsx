import { useMemo, useState } from 'react'
import { Bot, RotateCcw, Save } from 'lucide-react'
import {
  defaultCopilotSettings, FOUNDRY_ENV_DEFAULTS, loadCopilotSettings, resetCopilotSettings,
  saveCopilotSettings, type CopilotSettings,
} from '../services/copilot/settings'

const runtimeEnv = (import.meta as { env?: Record<string, string | undefined> }).env ?? {}
const hostedInvocationUrl = runtimeEnv.VITE_RAYFIN_FOUNDRY_INVOCATIONS_URL ?? ''
const hostedRuntimeVersion = runtimeEnv.VITE_RAYFIN_ORCHESTRATOR_VERSION ?? ''
const hostedImageDigest = runtimeEnv.VITE_RAYFIN_ORCHESTRATOR_IMAGE_DIGEST ?? ''
const hostedSourceDigest = runtimeEnv.VITE_RAYFIN_ORCHESTRATOR_SOURCE_DIGEST ?? ''

export function CopilotSettingsPanel() {
  const defaults = useMemo(() => defaultCopilotSettings(), [])
  const [saved, setSaved] = useState<CopilotSettings>(() => loadCopilotSettings())
  const [draft, setDraft] = useState<CopilotSettings>(saved)
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(saved), [draft, saved])

  const apply = () => { saveCopilotSettings(draft); setSaved(draft) }
  const restore = () => { const value = resetCopilotSettings(); setSaved(value); setDraft(value) }

  return <details className="copilot-settings">
    <summary>
      <span><Bot size={17} /></span>
      <div>
        <h2>Agent runtime settings</h2>
        <p>Deployment-owned Agent Framework configuration and optional agent comparison.</p>
      </div>
    </summary>
    <div className="copilot-settings-body">
      <p>Chief and specialist instructions, skills, tools and source catalogs are deployment-owned.
        This browser cannot redefine their capabilities, grant Entra permissions or authorize work-order creation.</p>

      {!hostedInvocationUrl ? <p className="copilot-settings-warning">
        The deployment does not contain a Hosted Agent invocation endpoint. Chat is unavailable until the canonical deployment completes.
      </p> : null}
      {!draft.projectEndpoint ? <p className="copilot-settings-warning">
        Set the Foundry project endpoint below to enable the provisioned agents. The signed-in user also needs the
        <code>Foundry User</code> role on that project.
      </p> : null}

      <div className="copilot-settings-group">
        <h4>Agent comparison</h4>
        <p>Enable the hidden Battle mode inside Hydro Intelligence for this browser. It remains off by default and does not initialize either agent during app startup.</p>
        <ul><li><label>
          <input
            type="checkbox"
            checked={draft.battleEnabled}
            onChange={event => setDraft(current => ({ ...current, battleEnabled: event.target.checked }))}
          />
          <span><strong>Battle of the Agents</strong><small>Send the same prompt through the hosted runtime to Fabric Data Agent and Foundry specialists, then compare their answers, traces, visuals, and latency side by side.</small></span>
        </label></li></ul>
      </div>

      <div className="copilot-settings-group">
        <h4>Foundry Agent Service
          <button type="button" className="copilot-settings-inline" disabled={draft.projectEndpoint === defaults.projectEndpoint} onClick={() => setDraft(current => ({ ...current, projectEndpoint: FOUNDRY_ENV_DEFAULTS.projectEndpoint }))}>Restore deployed project</button>
        </h4>
        <p>The Hosted Agent runs the Microsoft Agent Framework workflow. Chief coordinates Q&amp;A, RCA, Work Order, and Fabric IQ specialists; the deployment orchestrator owns their definitions and model selection.</p>
        <div className="copilot-settings-fields">
          <label>
            <span>Project endpoint</span>
            <input type="url" value={draft.projectEndpoint} placeholder="https://resource.services.ai.azure.com/api/projects/project" onChange={event => setDraft(current => ({ ...current, projectEndpoint: event.target.value }))} />
          </label>
          {hostedInvocationUrl && <label>
            <span>Hosted Agent invocation endpoint</span>
            <input type="url" value={hostedInvocationUrl} readOnly />
          </label>}
          {hostedRuntimeVersion && <label>
            <span>Hosted Agent version</span>
            <input type="text" value={hostedRuntimeVersion} readOnly />
          </label>}
          {hostedImageDigest && <label>
            <span>Immutable runtime image digest</span>
            <input type="text" value={hostedImageDigest} readOnly />
          </label>}
          {hostedSourceDigest && <label>
            <span>Verified source configuration digest</span>
            <input type="text" value={hostedSourceDigest} readOnly />
          </label>}
        </div>
      </div>

      <footer>
        <button type="button" className="copilot-settings-primary" disabled={!dirty} onClick={apply}>
          <Save size={14} />{dirty ? 'Save changes' : 'Saved'}
        </button>
        <button type="button" disabled={JSON.stringify(draft) === JSON.stringify(defaults)} onClick={restore}>
          <RotateCcw size={14} />Restore deployed defaults
        </button>
      </footer>
    </div>
  </details>
}
