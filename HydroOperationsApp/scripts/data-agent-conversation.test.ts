import assert from 'node:assert/strict'
import test from 'node:test'
import { contextualizeDataAgentQuestion } from '../src/services/dataAgentConversation'
import { extractDataAgentVisualizations } from '../src/services/dataAgentVisualizations'

test('standalone Data Agent questions remain byte-for-byte unchanged', () => {
  const question = 'Summarize all facilities and open work orders.'
  assert.equal(contextualizeDataAgentQuestion(question, ['Tell me about T004.']), question)
})

test('referential follow-ups include only a bounded recent user-question context', () => {
  const previous = [
    'oldest question that should be dropped',
    "What's the power output for T04 over the past 6 hours? Not avg",
    'or summarize as min/max and latest value without averaging',
    'Include quality.',
    'Keep the timestamps.',
  ]
  const result = contextualizeDataAgentQuestion('Can you plot the timeseries data for this asset?', previous)
  assert.doesNotMatch(result, /oldest question/)
  assert.match(result, /What's the power output/)
  assert.match(result, /or summarize as min\/max/)
  assert.match(result, /<current_user_question>\nCan you plot the timeseries data for this asset\?/)
})

test('Data Agent fenced time-series CSV becomes a local line visualization', () => {
  const answer = [
    'Plot-ready data:',
    '```csv',
    'event_time,value,quality',
    '2026-10-03T08:30:48,1847.485,GOOD',
    '2026-10-03T08:31:24,2303.502,BAD',
    '```',
  ].join('\n')
  assert.deepEqual(extractDataAgentVisualizations(answer, 'Plot the time series.'), [{
    chartType: 'line',
    title: 'Data Agent time series',
    xColumn: 'event_time',
    yColumns: ['value'],
    xAxisTitle: 'event_time',
    yAxisTitle: 'value',
    groupBy: undefined,
    inlineCsvData: 'event_time,value,quality\n2026-10-03T08:30:48,1847.485,GOOD\n2026-10-03T08:31:24,2303.502,BAD',
  }])
})

test('multi-signal Data Agent CSV renders one grouped series per OPC UA node', () => {
  const answer = [
    '```csv',
    'event_time,opcua_node_id,value,quality',
    '2026-10-03T08:30:48,ns=2;s=T005.power_output,1847.485,GOOD',
    '2026-10-03T08:30:48,ns=2;s=T005.turbine_speed,1501.779,GOOD',
    '```',
  ].join('\n')
  const [visualization] = extractDataAgentVisualizations(answer, 'Show all signals visually.')
  assert.equal(visualization.chartType, 'line')
  assert.equal(visualization.groupBy, 'opcua_node_id')
  assert.deepEqual(visualization.yColumns, ['value'])
})
