type WorkspaceItem = { id: string; type: string; displayName: string }

export function selectDataAgent(items: WorkspaceItem[]): WorkspaceItem | undefined {
  const dataAgents = items.filter(item => item.type === 'DataAgent')
  const versioned = dataAgents
    .filter(item => item.displayName.startsWith('RTI_Demo_Agent_'))
    .sort((left, right) => right.displayName.localeCompare(left.displayName, undefined, { numeric: true, sensitivity: 'base' }))
  return versioned[0] ?? (dataAgents.length === 1 ? dataAgents[0] : undefined)
}