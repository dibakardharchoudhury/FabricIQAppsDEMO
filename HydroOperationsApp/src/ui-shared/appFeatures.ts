type TabWithId = { id: string }

export function featureFlagEnabled(value: string | undefined): boolean {
  return value === 'true'
}

export const ENERGY_MAP_ENABLED = featureFlagEnabled(
  import.meta.env?.VITE_RAYFIN_ENABLE_ENERGY_MAP,
)

export function filterEnergyMapTabs<T extends TabWithId>(
  tabs: readonly T[],
  enabled = ENERGY_MAP_ENABLED,
): T[] {
  return tabs.filter(tab => enabled || tab.id !== 'map')
}

export function resolveVisibleTab<T extends TabWithId>(
  tabs: readonly T[],
  value: string | null,
): T {
  const fallback = tabs[0]
  if (!fallback) throw new Error('At least one application tab must be configured.')
  return tabs.find(tab => tab.id === value) ?? fallback
}
