import { useMemo, useState } from 'react'
import { Bot, RotateCcw, Save } from 'lucide-react'
import { ASSET_ENTITIES, catalogPrompt, KUSTO_SOURCES, OPERATIONS_ENTITIES, type CatalogEntity } from '../services/copilot/catalog'
import { isFoundryConfigured } from '../services/copilot/foundry'
import { TOOL_DEFINITIONS } from '../services/copilot/tools'
import {
  defaultCopilotSettings, loadCopilotSettings, resetCopilotSettings, saveCopilotSettings, type CopilotSettings,
} from '../services/copilot/settings'

type Toggle = { key: string; label: string; hint: string }

const entityToggles = (entities: CatalogEntity[]): Toggle[] => entities.map(entity => ({
  key: entity.key,
  label: entity.key,
  hint: `${entity.physicalName} — ${entity.description}`,
}))

export function CopilotSettingsPanel() {
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

  return <section className="copilot-settings">
    <header>
      <span><Bot size={17} /></span>
      <div>
        <h2>Foundry Copilot</h2>
        <p>Controls what the Foundry engine may read and how it is instructed. Saved in this browser and applied to the next question.</p>
      </div>
    </header>

    {!isFoundryConfigured() && <p className="copilot-settings-warning">
      The Foundry engine is not configured, so these settings have no effect yet. Set
      <code>RAYFIN_PUBLIC_FOUNDRY_ENDPOINT</code> and <code>RAYFIN_PUBLIC_FOUNDRY_DEPLOYMENT</code>, then rebuild.
    </p>}

    <div className="copilot-settings-group">
      <h4>Additional instructions</h4>
      <p>Appended to the built-in system prompt. Use it for tone, domain shorthand, or house rules — the safety rules and the data schema are always included.</p>
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
      <summary>Preview the schema sent to the model</summary>
      <pre>{catalogPrompt(draft)}</pre>
    </details>

    <footer>
      <button type="button" className="copilot-settings-primary" disabled={!dirty} onClick={apply}>
        <Save size={14} />{dirty ? 'Save changes' : 'Saved'}
      </button>
      <button type="button" disabled={JSON.stringify(draft) === JSON.stringify(defaultCopilotSettings())} onClick={restore}>
        <RotateCcw size={14} />Reset to defaults
      </button>
    </footer>
  </section>
}
