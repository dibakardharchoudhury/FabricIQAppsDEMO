import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultCopilotSettings, DEFAULT_SYSTEM_PROMPT, mergeCopilotSettings, renderCoordinatorPrompt, renderSystemPrompt } from '../src/services/copilot/settings.ts'
import { ANSWER_PRESENTATION_CONTRACT } from '../src/services/copilot/answerPresentation.ts'

test('Battle of the Agents is disabled by default', () => {
  assert.equal(defaultCopilotSettings().battleEnabled, false)
  assert.equal(mergeCopilotSettings(undefined).battleEnabled, false)
})

test('Battle of the Agents is enabled only by an explicit true setting', () => {
  assert.equal(mergeCopilotSettings({ battleEnabled: true }).battleEnabled, true)
  assert.equal(mergeCopilotSettings({ battleEnabled: false }).battleEnabled, false)
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
