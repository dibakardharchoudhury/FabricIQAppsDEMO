import type { AgentTraceEntry, OrchestrationEvent } from './orchestration.ts'

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value))

export function applicationInsightsLink(resourceId: unknown): string | undefined {
  if (typeof resourceId !== 'string'
    || !/^\/subscriptions\/[a-f0-9-]{36}\/resourceGroups\/[^/]+\/providers\/microsoft\.insights\/components\/[^/]+$/i.test(resourceId)) return undefined
  return `https://portal.azure.com/#resource${resourceId.split('/').map(encodeURIComponent).join('/')}`
}

export function responseTraceQuery(responseIds: string[]): string | undefined {
  const ids = [...new Set(responseIds)].filter(id => /^resp_[a-zA-Z0-9_-]+$/.test(id))
  if (!ids.length) return undefined
  return `dependencies\n| where timestamp > ago(24h)\n| where tostring(customDimensions["gen_ai.response.id"]) in (${ids.map(id => JSON.stringify(id)).join(', ')})\n| project timestamp, name, duration, success, operation_Id, id, operation_ParentId\n| order by timestamp asc`
}

export function executionStatus(events: OrchestrationEvent[]) {
  if (events.some(event => event.status === 'error')) return 'error'
  if (events.some(event => event.status === 'queued' || event.status === 'running')) return 'running'
  if (events.some(event => event.status === 'approval')) return 'approval'
  return events.length ? 'completed' : 'idle'
}

export function crewCommunications(events: OrchestrationEvent[]) {
  return events.flatMap(event => {
    const parent = events.find(parent => parent.id === event.parentId)
    if (!parent || parent.role !== 'supervisor' || event.role === 'supervisor') return []
    const receipt = event.parentCallId ? parent.trace?.find(entry =>
      entry.activity === 'delegation-return' && entry.callId === event.parentCallId) : undefined
    return [{
      id: event.id, role: event.role, from: parent.label, to: event.label,
      sentAt: event.timestamp, receivedAt: receipt?.timestamp,
      failed: receipt?.failed === true,
      waiting: !receipt && (event.status === 'queued' || event.status === 'running'),
    }]
  })
}

export function crewHandoffs(events: OrchestrationEvent[]) {
  return crewCommunications(events).flatMap(flow => [
    { id: `${flow.id}:sent`, from: flow.from, to: flow.to, timestamp: flow.sentAt, failed: false },
    ...(flow.receivedAt === undefined ? [] : [{
      id: `${flow.id}:returned`, from: flow.to, to: flow.from, timestamp: flow.receivedAt, failed: flow.failed,
    }]),
  ]).sort((a, b) => a.timestamp - b.timestamp)
}

export function captureFoundryEvent(event: OrchestrationEvent, raw: unknown, timestamp = Date.now()): boolean {
  if (!record(raw) || typeof raw.type !== 'string') return false
  const response = record(raw.response) ? raw.response : undefined
  const item = record(raw.item) ? raw.item : undefined
  let label: string | undefined
  let failed = false
  let activity: AgentTraceEntry['activity']
  if (raw.type === 'response.created') {
    if (typeof response?.id === 'string') {
      event.responseId = response.id
      event.responseIds = [...new Set([...(event.responseIds ?? []), response.id])]
    }
    event.status = 'running'
    label = 'Foundry accepted the invocation'
  } else if (raw.type === 'response.completed') {
    label = 'Foundry response completed'
  } else if (raw.type === 'response.failed' || raw.type === 'response.incomplete' || raw.type === 'error') {
    label = 'Foundry reported an execution failure'
    failed = true
  } else if (raw.type === 'response.output_item.done' && item?.type === 'function_call') {
    label = `Requested function: ${String(item.name ?? 'unnamed')}`
  } else if (raw.type === 'response.output_item.done' && item?.type === 'mcp_call') {
    activity = 'tool-end'
    failed = Boolean(item.error) || item.status === 'failed'
    label = `${failed ? 'Failed' : 'Returned'} remote tool: ${String(item.server_label ?? '')} / ${String(item.name ?? 'unnamed')}`
  } else if (raw.type === 'response.mcp_call.in_progress') {
    activity = 'tool-start'
    label = 'Fabric IQ remote tool is executing'
  }
  if (!label) return false
  event.detail = label
  event.trace = [...(event.trace ?? []), {
    id: `${event.id}:${event.trace?.length ?? 0}`, timestamp, source: 'foundry',
    label, responseId: event.responseId, failed, activity,
    ...(typeof item?.call_id === 'string' ? { callId: item.call_id } : {}),
  }]
  return true
}

export function captureApplicationEvent(event: OrchestrationEvent, label: string, callId?: string, failed = false, activity?: AgentTraceEntry['activity']) {
  event.trace = [...(event.trace ?? []), {
    id: `${event.id}:${event.trace?.length ?? 0}`, timestamp: Date.now(),
    source: 'application', label, callId, failed, activity, responseId: event.responseId,
  }]
}
