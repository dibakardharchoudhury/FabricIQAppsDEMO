import type { AgentVisualization } from './assistantStream'

export type AssistantTextContent = {
  type?: string
  text?: { value?: string }
  refusal?: string
}

export function extractAssistantText(messages: Array<{ content?: AssistantTextContent[] }>): string {
  return messages
    .flatMap(message => message.content ?? [])
    .map(part => part.text?.value ?? part.refusal ?? '')
    .filter(text => text.length > 0)
    .join('\n\n')
}

export type AssistantRunStep = {
  step_details?: {
    tool_calls?: Array<{
      id?: string
      function?: { name?: string; arguments?: string }
    }>
  }
}

export function extractAgentVisualizations(steps: AssistantRunStep[]): AgentVisualization[] {
  const callIds = new Set<string>()
  const visualizations: AgentVisualization[] = []
  for (const step of steps) {
    for (const toolCall of step.step_details?.tool_calls ?? []) {
      if (toolCall.function?.name !== 'AIFunction.VisualizeDataset' || !toolCall.function.arguments) continue
      if (toolCall.id && callIds.has(toolCall.id)) continue
      try {
        const value = JSON.parse(toolCall.function.arguments) as {
          chart_type?: string
          title?: string
          x_column?: string
          y_columns?: string[]
          x_axis_title?: string
          y_axis_title?: string
          group_by?: string
          sort_by?: string
          sort_order?: string
          inline_csv_data?: string
        }
        if (!value.chart_type || !value.x_column || !value.y_columns?.length || !value.inline_csv_data) continue
        visualizations.push({
          chartType: value.chart_type,
          title: value.title || 'Data Agent visualization',
          xColumn: value.x_column,
          yColumns: value.y_columns,
          xAxisTitle: value.x_axis_title,
          yAxisTitle: value.y_axis_title,
          groupBy: value.group_by,
          sortBy: value.sort_by,
          sortOrder: value.sort_order,
          inlineCsvData: value.inline_csv_data,
        })
        if (toolCall.id) callIds.add(toolCall.id)
      } catch {
        // Preserve the assistant response even when optional tool metadata is malformed.
      }
    }
  }
  return visualizations
}
