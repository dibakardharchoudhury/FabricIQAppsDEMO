import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultCopilotSettings, mergeCopilotSettings } from '../src/services/copilot/settings.ts'

test('Battle of the Agents is disabled by default', () => {
  assert.equal(defaultCopilotSettings().battleEnabled, false)
  assert.equal(mergeCopilotSettings(undefined).battleEnabled, false)
})

test('Battle of the Agents is enabled only by an explicit true setting', () => {
  assert.equal(mergeCopilotSettings({ battleEnabled: true }).battleEnabled, true)
  assert.equal(mergeCopilotSettings({ battleEnabled: false }).battleEnabled, false)
})
