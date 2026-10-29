import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { defaultCopilotSettings, DEFAULT_SYSTEM_PROMPT, mergeCopilotSettings, renderCoordinatorPrompt, renderSystemPrompt } from '../src/services/copilot/settings.ts'
import { ANSWER_PRESENTATION_CONTRACT } from '../src/services/copilot/answerPresentation.ts'

test('default weather sample uses UKMet rather than assuming optional Aurora coverage', () => {
  const source = readFileSync(new URL('../src/components/CopilotExperience.tsx', import.meta.url), 'utf8')
  assert.ok(source.includes("'Compare the latest UKMet forecasts with observations for the same facilities. Keep vendors, forecast issues, valid times and units separate.'"))
  assert.doesNotMatch(source, /Compare Aurora and UKMet rainfall forecasts/)
})

test('retired Battle preferences are discarded without changing supported settings', () => {
  const legacy = {
    battleEnabled: true,
    projectEndpoint: 'https://test.services.ai.azure.com/api/projects/hydro',
    promptExtra: 'Preserve operator instructions.',
  }
  for (const settings of [defaultCopilotSettings(), mergeCopilotSettings(undefined), mergeCopilotSettings(legacy)]) {
    assert.equal(Object.hasOwn(settings, 'battleEnabled'), false)
  }
  assert.equal(mergeCopilotSettings(legacy).projectEndpoint, legacy.projectEndpoint)
  assert.equal(mergeCopilotSettings(legacy).promptExtra, legacy.promptExtra)
})

test('previous shipped prompt upgrades while operator customization remains intact', () => {
  const previousDefault = DEFAULT_SYSTEM_PROMPT.split('\n\nAdaptive response contract:')[0].replace(
    '- Queries are read-only. Work-order proposals require human review and approval in the application before any SQL write.',
    '- You are read-only. You cannot create, modify or delete anything; say so if asked.',
  )
  assert.equal(mergeCopilotSettings({ systemPrompt: previousDefault }).systemPrompt, DEFAULT_SYSTEM_PROMPT)
  assert.equal(mergeCopilotSettings({ systemPrompt: 'Use the approved operator policy.' }).systemPrompt, 'Use the approved operator policy.')
})

test('coordinator and direct specialists receive operator instructions and the shared output contract', () => {
  const settings = { ...defaultCopilotSettings(), promptExtra: '  Use explicit units in every measurement column.  ' }
  const now = new Date('2026-10-07T17:00:00Z')
  for (const context of [renderCoordinatorPrompt(settings, now), renderSystemPrompt(settings, 'test schema', now)]) {
    assert.match(context, /Additional operator instructions:\nUse explicit units in every measurement column\./)
    assert.ok(context.includes(ANSWER_PRESENTATION_CONTRACT))
    assert.ok(context.includes(now.toISOString()))
  }
  assert.doesNotMatch(renderCoordinatorPrompt({ promptExtra: '   ' }, now), /Additional operator instructions/)
})
