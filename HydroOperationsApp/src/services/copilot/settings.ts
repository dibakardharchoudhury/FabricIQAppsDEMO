import { ASSET_ENTITIES, KUSTO_SOURCES, OPERATIONS_ENTITIES } from './catalog.ts'

// Operator-tunable copilot configuration, edited in Administration and persisted per browser.
// It narrows what the MODEL may reach; it is not a security boundary against the signed-in user,
// who is always limited to their own Entra permissions by the delegated token.

export const TOOL_NAMES = ['query_assets', 'query_operations', 'query_telemetry', 'run_kql', 'visualize_dataset'] as const
export type ToolName = (typeof TOOL_NAMES)[number]

export type CopilotSettings = {
  promptExtra: string
  tools: Record<string, boolean>
  entities: Record<string, boolean>
  kustoSources: Record<string, boolean>
}

const STORAGE_KEY = 'hydro.copilot.settings.v1'

const allEnabled = (keys: readonly string[]) => Object.fromEntries(keys.map(key => [key, true]))

export const ENTITY_KEYS = [...ASSET_ENTITIES, ...OPERATIONS_ENTITIES].map(entity => entity.key)
export const KUSTO_NAMES = KUSTO_SOURCES.map(source => source.name)

export function defaultCopilotSettings(): CopilotSettings {
  return {
    promptExtra: '',
    tools: allEnabled(TOOL_NAMES),
    entities: allEnabled(ENTITY_KEYS),
    kustoSources: allEnabled(KUSTO_NAMES),
  }
}

/** Merge stored values over the defaults so a new tool or table defaults to enabled. */
export function mergeCopilotSettings(stored: Partial<CopilotSettings> | null | undefined): CopilotSettings {
  const defaults = defaultCopilotSettings()
  if (!stored) return defaults
  return {
    promptExtra: typeof stored.promptExtra === 'string' ? stored.promptExtra : defaults.promptExtra,
    tools: { ...defaults.tools, ...(stored.tools ?? {}) },
    entities: { ...defaults.entities, ...(stored.entities ?? {}) },
    kustoSources: { ...defaults.kustoSources, ...(stored.kustoSources ?? {}) },
  }
}

export function loadCopilotSettings(): CopilotSettings {
  try { return mergeCopilotSettings(JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') as Partial<CopilotSettings>) }
  catch { return defaultCopilotSettings() }
}

export function saveCopilotSettings(settings: CopilotSettings): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)) }
  catch { /* storage unavailable */ }
}

export function resetCopilotSettings(): CopilotSettings {
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* storage unavailable */ }
  return defaultCopilotSettings()
}

export const isToolEnabled = (settings: CopilotSettings, name: string) => settings.tools[name] !== false
export const isEntityEnabled = (settings: CopilotSettings, key: string) => settings.entities[key] !== false
export const isKustoSourceEnabled = (settings: CopilotSettings, name: string) => settings.kustoSources[name] !== false
export const enabledKustoNames = (settings: CopilotSettings) => KUSTO_NAMES.filter(name => isKustoSourceEnabled(settings, name))
