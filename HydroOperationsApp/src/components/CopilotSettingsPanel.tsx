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
        <h2>Foundry Copilot</h2>
        <p>Prompt, tools and data sources. Saved in this browser and applied to the next question.</p>
      </div>
    </summary>
    <div className="copilot-settings-body">

    {!draft.endpoint || !draft.deployment ? <p className="copilot-settings-warning">
      Set the endpoint and deployment below to enable the Foundry engine. The signed-in user also needs the
      <code>Cognitive Services OpenAI User</code> role on that resource.
    </p> : null}
    <div className="copilot-settings-group">
      <h4>Model endpoint
        <button type="button" className="copilot-settings-inline" disabled={draft.endpoint === defaults.endpoint && draft.deployment === defaults.deployment} onClick={() => setDraft(current => ({ ...current, ...FOUNDRY_ENV_DEFAULTS }))}>Restore from rayfin/.env</button>
      </h4>
      <p>Use the model inference URL ending in <code>/openai/v1/responses</code>, not the Foundry project URL.</p>
      <div className="copilot-settings-fields">
        <label>
          <span>Endpoint</span>
          <input
            type="url"
            value={draft.endpoint}
            spellCheck={false}
            placeholder="https://<resource>.services.ai.azure.com/openai/v1/responses"
            onChange={event => setDraft(current => ({ ...current, endpoint: event.target.value }))}
          />
        </label>
        <label>
          <span>Deployment</span>
          <input
            value={draft.deployment}
            spellCheck={false}
            placeholder="gpt-5-mini"
            onChange={event => setDraft(current => ({ ...current, deployment: event.target.value }))}
          />
        </label>
      </div>
    </div>

    <div className="copilot-settings-group">
      <h4>System prompt
        <button type="button" className="copilot-settings-inline" disabled={draft.systemPrompt === defaults.systemPrompt} onClick={() => setDraft(current => ({ ...current, systemPrompt: defaults.systemPrompt }))}>Restore default</button>
      </h4>
      <p>
        The base instructions sent with every question. <code>{CATALOG_PLACEHOLDER}</code> is replaced with the
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
      <p>Appended after the system prompt. Use it for tone, domain shorthand, or house rules.</p>
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
      <summary>Preview the full prompt sent to the model</summary>
      <pre>{renderSystemPrompt(draft, catalogPrompt(draft))}</pre>
    </details>

    <footer>
      <button type="button" className="copilot-settings-primary" disabled={!dirty} onClick={apply}>
        <Save size={14} />{dirty ? 'Save changes' : 'Saved'}
      </button>
      <button type="button" disabled={JSON.stringify(draft) === JSON.stringify(defaults)} onClick={restore}>
        <RotateCcw size={14} />Reset to defaults
      </button>
    </footer>
    </div>
  </details>
}
