type WorkspaceItem = { id: string; type: string; displayName: string }

export function selectDataAgent(items: WorkspaceItem[]): WorkspaceItem | undefined {
  const dataAgents = items.filter(item => item.type === 'DataAgent')
  const versioned = dataAgents
    .filter(item => item.displayName.startsWith('RTI_Demo_Agent_'))
    .sort((left, right) => right.displayName.localeCompare(left.displayName, undefined, { numeric: true, sensitivity: 'base' }))
  return versioned[0] ?? (dataAgents.length === 1 ? dataAgents[0] : undefined)
}

export function mapDataAgentName(eventhouseName: string, configuredName?: string): string {
  if (configuredName?.trim()) return configuredName.trim()
  const suffix = eventhouseName.match(/^RTI_Demo_Eventhouse_([A-Za-z0-9_]+)$/)?.[1]
  if (!suffix) throw new Error('Configure an exact map Data Agent name for this workspace.')
  return `Hydro_Map_Agent_${suffix}`
}

export function selectNamedDataAgent(items: WorkspaceItem[], name: string): WorkspaceItem {
  const matches = items.filter(item => item.type === 'DataAgent' && item.displayName === name)
  if (matches.length !== 1) throw new Error(`Expected one published map Data Agent named ${name}; run map-agent provisioning.`)
  return matches[0]
}