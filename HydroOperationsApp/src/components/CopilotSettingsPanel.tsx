import { useMemo, useState } from 'react'
import { Bot, RotateCcw, Save } from 'lucide-react'
import { ASSET_ENTITIES, catalogPrompt, KUSTO_SOURCES, OPERATIONS_ENTITIES, type CatalogEntity } from '../services/copilot/catalog'
import { TOOL_DEFINITIONS } from '../services/copilot/tools'
import {
  CATALOG_PLACEHOLDER, defaultCopilotSettings, FOUNDRY_ENV_DEFAULTS, loadCopilotSettings, renderSystemPrompt,
  resetCopilotSettings, saveCopilotSettings, TIME_PLACEHOLDER, type CopilotSettings,
} from '../services/copilot/settings'

type Toggle = { key: string; label: string; hint: string }

const entityToggles = (entities: CatalogEntity[]): Toggle[] => entities.map(entity => ({
  key: entity.key,
  label: entity.key,
  hint: `${entity.physicalName} — ${entity.description}`,
}))

const runtimeEnv = (import.meta as { env?: Record<string, string | undefined> }).env ?? {}
const hostedInvocationUrl = runtimeEnv.VITE_RAYFIN_FOUNDRY_INVOCATIONS_URL ?? ''
const hostedSourceDigest = runtimeEnv.VITE_RAYFIN_ORCHESTRATOR_SOURCE_DIGEST ?? ''

export function CopilotSettingsPanel() {
  const defaults = useMemo(() => defaultCopilotSettings(), [])
  const [saved, setSaved] = useState<CopilotSettings>(() => loadCopilotSettings())
  const [draft, setDraft] = useState<CopilotSettings>(saved)
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(saved), [draft, saved])

  const setFlag = (group: 'tools' | 'entities' | 'kustoSources', key: string, value: boolean) =>
    setDraft(current => ({ ...current, [group]: { ...current[group], [key]: value } }))

  const apply = () => { saveCopilotSettings(draft); setSaved(draft) }
  const restore = () => { const defaults = resetCopilotSettings(); setSaved(defaults); setDraft(defaults) }

  const group = (
    title: string,
    note: string,
    toggles: Toggle[],
    field: 'tools' | 'entities' | 'kustoSources',
  ) => <div className="copilot-settings-group">
    <h4>{title}</h4>
    <p>{note}</p>
    <ul>{toggles.map(toggle => <li key={toggle.key}>
      <label>
        <input
          type="checkbox"
          checked={draft[field][toggle.key] !== false}
          onChange={event => setFlag(field, toggle.key, event.target.checked)}
        />
        <span><strong>{toggle.label}</strong><small>{toggle.hint}</small></span>
      </label>
    </li>)}</ul>
  </div>

  return <details className="copilot-settings">
    <summary>
      <span><Bot size={17} /></span>
      <div>
        <h2>{hostedInvocationUrl ? 'Agent runtime settings' : 'Advanced agent settings'}</h2>
        <p>{hostedInvocationUrl
          ? 'Deployment-owned Agent Framework configuration and optional agent comparison.'
          : 'Optional browser overrides and agent comparison. Deployed defaults work without editing these settings.'}</p>
      </div>
    </summary>
    <div className="copilot-settings-body">
    <p>{hostedInvocationUrl
      ? 'Chief and specialist instructions, skills, tools and source catalogs are deployment-owned. This browser cannot redefine their capabilities, grant Entra permissions or authorize work-order creation.'
      : <>These controls restrict the agent's available tools and sources; they do not grant Entra permissions,
        change Fabric access, or authorize work-order creation. Human approval is still required.
        Use them for troubleshooting or deliberate customization, not routine sign-in.</>}</p>

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
        <span><strong>Battle of the Agents</strong><small>Send the same prompt to Fabric Data Agent and Foundry, then compare their answers, traces, visuals, and latency side by side.</small></span>
      </label></li></ul>
    </div>
    <div className="copilot-settings-group">
      <h4>Foundry Agent Service
        <button type="button" className="copilot-settings-inline" disabled={draft.projectEndpoint === defaults.projectEndpoint} onClick={() => setDraft(current => ({ ...current, projectEndpoint: FOUNDRY_ENV_DEFAULTS.projectEndpoint }))}>Restore deployed project</button>
      </h4>
      <p>Every Foundry request goes through the persistent Hydro Supervisor, which delegates to Q&amp;A, RCA, Work Order, or Fabric IQ specialists. The deployment orchestrator provisions their definitions and model selection; no separate model-inference endpoint is used here.</p>
      <div className="copilot-settings-fields">
        <label>
          <span>Project endpoint</span>
          <input type="url" value={draft.projectEndpoint} placeholder="https://resource.services.ai.azure.com/api/projects/project" onChange={event => setDraft(current => ({ ...current, projectEndpoint: event.target.value }))} />
        </label>
        {hostedInvocationUrl && <label>
          <span>Hosted Agent invocation endpoint</span>
          <input type="url" value={hostedInvocationUrl} readOnly />
        </label>}
        {hostedSourceDigest && <label>
          <span>Verified source configuration digest</span>
          <input type="text" value={hostedSourceDigest} readOnly />
        </label>}
      </div>
    </div>

    {!hostedInvocationUrl && <>
    <div className="copilot-settings-group">
      <h4>Direct-source specialist prompt
        <button type="button" className="copilot-settings-inline" disabled={draft.systemPrompt === defaults.systemPrompt} onClick={() => setDraft(current => ({ ...current, systemPrompt: defaults.systemPrompt }))}>Restore default</button>
      </h4>
      <p>
        The context sent to Q&amp;A, RCA, and Work Order specialists. <code>{CATALOG_PLACEHOLDER}</code> is replaced with the
        enabled schema and <code>{TIME_PLACEHOLDER}</code> with the current timestamp.
      </p>
      <textarea
        className="copilot-settings-code"
        value={draft.systemPrompt}
        rows={14}
        spellCheck={false}
        onChange={event => setDraft(current => ({ ...current, systemPrompt: event.target.value }))}
      />
      {!draft.systemPrompt.includes(CATALOG_PLACEHOLDER) && <p className="copilot-settings-warning">
        Without <code>{CATALOG_PLACEHOLDER}</code> the model receives no table schema and will not know what it can query.
      </p>}
    </div>

    <div className="copilot-settings-group">
      <h4>Additional instructions</h4>
      <p>Included for the Supervisor and every Foundry specialist. Use it for tone, domain shorthand, or house rules; registered agent permissions and human approval requirements still apply.</p>
      <textarea
        value={draft.promptExtra}
        rows={4}
        placeholder="e.g. Always report power in MW and prefer the last 24 hours unless asked otherwise."
        onChange={event => setDraft(current => ({ ...current, promptExtra: event.target.value }))}
      />
    </div>

    {group('Tools', 'Disabled tools are removed from the model\u2019s schema and refused if called anyway.',
      TOOL_DEFINITIONS.map(tool => ({ key: tool.function.name, label: tool.function.name, hint: tool.function.description })),
      'tools')}

    {group('Lakehouse tables', 'Asset metadata reachable through query_assets.', entityToggles(ASSET_ENTITIES), 'entities')}

    {group('Operational tables', 'App database records reachable through query_operations.', entityToggles(OPERATIONS_ENTITIES), 'entities')}

    {group('Eventhouse tables & functions', 'Kusto sources reachable through query_telemetry and run_kql. run_kql rejects any query that does not start with an enabled source.',
      KUSTO_SOURCES.map(source => ({ key: source.name, label: source.name, hint: source.description })),
      'kustoSources')}

    <details className="copilot-settings-preview">
      <summary>Preview direct-source specialist context</summary>
      <pre>{renderSystemPrompt(draft, catalogPrompt(draft))}</pre>
    </details>
    </>}

    <footer>
      <button type="button" className="copilot-settings-primary" disabled={!dirty} onClick={apply}>
        <Save size={14} />{dirty ? 'Save changes' : 'Saved'}
      </button>
      {!hostedInvocationUrl && <button type="button" disabled={JSON.stringify(draft) === JSON.stringify(defaults)} onClick={restore}>
        <RotateCcw size={14} />Reset to defaults
      </button>}
    </footer>
    </div>
  </details>
}
