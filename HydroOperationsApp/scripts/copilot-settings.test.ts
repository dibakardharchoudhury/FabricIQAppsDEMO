import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultCopilotSettings, DEFAULT_SYSTEM_PROMPT, mergeCopilotSettings } from '../src/services/copilot/settings.ts'

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
