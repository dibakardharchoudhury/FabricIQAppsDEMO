import assert from 'node:assert/strict'
import test from 'node:test'
import { extractAgentVisualizations, extractAssistantText } from '../src/services/agentResponse.ts'

const argumentsJson = JSON.stringify({
  inline_csv_data: 'event_time,signal,value\n2026-09-03T09:01:00Z,power_output,2467.048',
  x_column: 'event_time',
  y_columns: ['value'],
  chart_type: 'line_chart',
  title: 'Turbine 6 key telemetry',
  x_axis_title: 'Time (last 24 hours)',
  y_axis_title: 'Value (mixed units)',
  sort_by: 'event_time',
  sort_order: 'asc',
  group_by: 'signal',
})

test('preserves the exact Fabric VisualizeDataset specification', () => {
  const result = extractAgentVisualizations([
    { step_details: { tool_calls: [{ id: 'call-1', function: { name: 'trace.VisualizeDataset', arguments: argumentsJson } }] } },
    { step_details: { tool_calls: [{ id: 'call-1', function: { name: 'AIFunction.VisualizeDataset', arguments: argumentsJson } }] } },
    { step_details: { tool_calls: [{ id: 'call-1', function: { name: 'AIFunction.VisualizeDataset', arguments: argumentsJson } }] } },
  ])

  assert.deepEqual(result, [{
    chartType: 'line_chart',
    title: 'Turbine 6 key telemetry',
    xColumn: 'event_time',
    yColumns: ['value'],
    xAxisTitle: 'Time (last 24 hours)',
    yAxisTitle: 'Value (mixed units)',
    groupBy: 'signal',
    sortBy: 'event_time',
    sortOrder: 'asc',
    inlineCsvData: 'event_time,signal,value\n2026-09-03T09:01:00Z,power_output,2467.048',
  }])
})

test('ignores malformed optional visualization metadata', () => {
  assert.deepEqual(extractAgentVisualizations([
    { step_details: { tool_calls: [{ id: 'bad', function: { name: 'AIFunction.VisualizeDataset', arguments: '{' } }] } },
  ]), [])
})

test('preserves every assistant text block in chronological order', () => {
  assert.equal(extractAssistantText([
    { content: [{ type: 'text', text: { value: 'First message, first block.' } }, { type: 'text', text: { value: 'First message, second block.' } }] },
    { content: [{ type: 'text', text: { value: 'Second message.' } }] },
  ]), 'First message, first block.\n\nFirst message, second block.\n\nSecond message.')
})

test('preserves refusal content returned instead of a text block', () => {
  assert.equal(extractAssistantText([{ content: [{ type: 'refusal', refusal: 'Unable to answer.' }] }]), 'Unable to answer.')
})
