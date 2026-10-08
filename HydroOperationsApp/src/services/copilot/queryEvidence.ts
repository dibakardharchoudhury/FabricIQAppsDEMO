import type { AgentStep } from '../agentSteps.ts'
import { KqlValidationError, readingFreshness, validateKql } from './query.ts'
import { cell } from './rcaEvidence.ts'

export type RequestedQueryCheck = { query: string; executable?: string; status: string }

export function checkRequestedKql(question: string, sources: string[]): RequestedQueryCheck[] {
  if (!/\b(?:check|validate|validation|correct|execute|run)\b/i.test(question)) return []
  const fenced = [...question.matchAll(/```(?:kql|kusto)\s*\r?\n([\s\S]*?)```/gi)].map(match => match[1].trim())
  const inline = question.match(/\bQuery:\s*(OPCUAEvents[\s\S]*)$/i)?.[1].trim()
  return [...new Set(fenced.length ? fenced : inline ? [inline] : [])].map(query => {
    try {
      return { query, executable: validateKql(query, sources),
        status: 'Passed local safety/pattern checks only; not a full KQL semantic validation.' }
    } catch (error) {
      if (!(error instanceof KqlValidationError)) throw error
      return { query, status: `Rejected by local validation: ${error.message}` }
    }
  })
}

export function renderQueryChecks(checks: readonly RequestedQueryCheck[], steps: readonly AgentStep[]): string {
  if (!checks.length) return ''
  const sections = ['## Query execution evidence']
  for (const check of checks) {
    const executed = check.executable && steps.some(step =>
      step.status === 'done' && step.tool === 'run_kql' && step.query === check.executable)
    sections.push(`${check.status}\n\nSupplied query executed through the bounded guard: **${executed ? 'yes' : 'no'}**. The guard may append its result limit. A corrected or typed equivalent is a separate query; it does not validate the original.`)
  }
  for (const step of steps.filter(step => step.status === 'done' && step.query && step.result)) {
    const data: unknown = JSON.parse(step.result!)
    if (!data || typeof data !== 'object' || !('rows' in data) || !Array.isArray(data.rows)) {
      throw new Error('Completed query receipt omitted source rows.')
    }
    const clock = 'read_completed_at_utc' in data ? data.read_completed_at_utc : undefined
    const rows: Record<string, unknown>[] = data.rows.map(row => {
      if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('Invalid source row in query execution receipt.')
      return row
    })
    const columns = [...new Set(rows.flatMap(row => Object.keys(row)))]
    const hasTime = columns.includes('event_time')
    sections.push(`### Executed ${cell(step.tool)}\n\n\`\`\`kql\n${step.query}\n\`\`\``,
      `Returned rows: ${rows.length}. Read completed: ${cell(clock ?? 'Not returned')}. ${'truncated' in data && data.truncated ? '**Source rows truncated; coverage is incomplete.**' : ''}`,
      rows.length ? [
        `| ${[...columns, ...(hasTime ? ['Freshness'] : [])].map(cell).join(' | ')} |`,
        `| ${[...columns, ...(hasTime ? ['Freshness'] : [])].map(() => '---').join(' | ')} |`,
        ...rows.map(row => `| ${[...columns.map(column => cell(row[column] ?? null)),
          ...(hasTime ? [cell(readingFreshness(row.event_time, clock))] : [])].join(' | ')} |`),
      ].join('\n') : 'No rows returned for this query; this is not proof that the equipment is healthy.')
  }
  if (!steps.some(step => step.status === 'done' && step.query)) sections.push('No source query execution receipt was returned.')
  return sections.join('\n\n')
}
