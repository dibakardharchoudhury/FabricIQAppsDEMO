import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { defaultCopilotSettings, DEFAULT_SYSTEM_PROMPT, loadCopilotSettings, mergeCopilotSettings, renderCoordinatorPrompt, renderSystemPrompt, resetCopilotSettings, saveCopilotSettings } from '../src/services/copilot/settings.ts'
import { ANSWER_PRESENTATION_CONTRACT } from '../src/services/copilot/answerPresentation.ts'

test('visibility defaults migrate existing settings and accept only actual boolean preferences', () => {
  assert.deepEqual(defaultCopilotSettings().intelligenceModeVisibility, { direct: true, multi: true, battle: true })
  assert.deepEqual(mergeCopilotSettings({ promptExtra: 'Keep operator policy.' }).intelligenceModeVisibility,
    { direct: true, multi: true, battle: true })
  const settings = mergeCopilotSettings(JSON.parse('{"intelligenceModeVisibility":{"direct":"false","multi":false}}'))
  assert.deepEqual(settings.intelligenceModeVisibility, { direct: true, multi: false, battle: true })
})

test('Admin visibility changes persist and resetting restores every mode without changing agent capabilities', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const storage = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  } })
  try {
    const settings = { ...defaultCopilotSettings(), intelligenceModeVisibility: { direct: false, multi: true, battle: false } }
    saveCopilotSettings(settings)
    assert.deepEqual(loadCopilotSettings().intelligenceModeVisibility, settings.intelligenceModeVisibility)
    assert.deepEqual(loadCopilotSettings().tools, settings.tools)
    assert.deepEqual(resetCopilotSettings().intelligenceModeVisibility, { direct: true, multi: true, battle: true })
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor)
    else Reflect.deleteProperty(globalThis, 'localStorage')
  }
})

test('mode choices live in the chat header and Admin exposes visibility checkboxes in both layouts', () => {
  const experience = readFileSync(new URL('../src/components/IntelligenceExperience.tsx', import.meta.url), 'utf8')
  const chat = readFileSync(new URL('../src/components/CopilotExperience.tsx', import.meta.url), 'utf8')
  const settings = readFileSync(new URL('../src/components/CopilotSettingsPanel.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(experience, /role="tablist"|v2-intelligence-tabs/)
  assert.match(experience, /summary ref=\{picker\} aria-label=\{`Chat mode:/)
  assert.match(chat, /className="v2-copilot-actions">\s*\{headerControls\}/)
  assert.match(settings, /type="checkbox" checked=\{draft\.intelligenceModeVisibility\[mode\.id\]\}/)
  assert.match(readFileSync(new URL('../src/ui-shared/pages/AdministrationPage.tsx', import.meta.url), 'utf8'),
    /<CopilotSettingsPanel/)
  for (const layout of ['ui-v1', 'ui-v2']) {
    assert.match(readFileSync(new URL(`../src/${layout}/pages/AdministrationPage.tsx`, import.meta.url), 'utf8'),
      /export \{ AdministrationPage \} from '\.\.\/\.\.\/ui-shared\/pages\/AdministrationPage'/)
    assert.match(readFileSync(new URL(`../src/${layout}/pages/CopilotPage.tsx`, import.meta.url), 'utf8'),
      /export \{ CopilotPage \} from '\.\.\/\.\.\/ui-shared\/pages\/CopilotPage'/)
  }
})

test('the removed static weather starter prompts are not restored by chat controls', () => {
  const source = readFileSync(new URL('../src/components/CopilotExperience.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /Compare the latest UKMet forecasts with observations for the same facilities|Compare Aurora and UKMet rainfall forecasts/)
})

test('conversation ownership spans both layouts and does not depend on refreshed data-cache state', () => {
  const data = readFileSync(new URL('../src/ui-shared/hooks/useHydroOperationsData.ts', import.meta.url), 'utf8')
  const page = readFileSync(new URL('../src/ui-shared/pages/CopilotPage.tsx', import.meta.url), 'utf8')
  const provider = readFileSync(new URL('../src/ui-shared/hooks/useIntelligenceConversations.ts', import.meta.url), 'utf8')
  const fabric = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
  const identity = fabric.slice(fabric.indexOf('export function intelligenceIdentityKey()'), fabric.indexOf('/** Resolve by verified published source identity'))
  assert.match(data, /createElement\(IntelligenceConversationsProvider, \{ userId: value\.user\?\.id \}, children\)/)
  for (const layout of ['ui-v1', 'ui-v2']) {
    const shell = readFileSync(new URL(`../src/${layout}/components/${layout === 'ui-v1' ? 'V1' : 'V2'}Shell.tsx`, import.meta.url), 'utf8')
    assert.match(shell, /<HydroOperationsDataProvider>.*<\/HydroOperationsDataProvider>/)
  }
  assert.match(page, /useIntelligenceConversations\(\)/)
  assert.doesNotMatch(page, /conversations\.clear|setIdentity|new IntelligenceConversations|useHydroOperationsData/)
  assert.match(provider, /useEffect\(\(\) => \(\) => conversations\.clear\(\), \[conversations\]\)/)
  assert.doesNotMatch(identity, /configRevision|configCache|ontologySourceConnectionKey/)
  assert.match(identity, /intelligenceOntologyId/)
  assert.match(identity, /selectedAccount\(\)\?\.localAccountId \?\? currentUser\(\)\?\.id/)
  assert.match(identity, /VITE_RAYFIN_LAKEHOUSE_ID.*VITE_RAYFIN_LAKEHOUSE_SQL_ENDPOINT_ID/)
  assert.match(identity, /VITE_RAYFIN_OPERATIONAL_SQL_DATABASE_ID/)
  assert.match(fabric, /semantic\.ontologyGeneration === 2 && !semantic\.ontologyError && semantic\.ontologyId/)
  assert.doesNotMatch(provider, /localStorage|sessionStorage|indexedDB/)
})

test('chat typography includes an automatic-height direct notice row and app-sized text controls', () => {
  const source = readFileSync(new URL('../src/components/CopilotExperience.tsx', import.meta.url), 'utf8')
  const css = readFileSync(new URL('../src/ui-shared/styles/app.css', import.meta.url), 'utf8')
  assert.ok(source.includes("direct ? ' has-direct-notice'"))
  assert.ok(/\.v2-copilot\.v2-expanded-view\.has-direct-notice[^}]*grid-template-rows: auto auto minmax\(0, 1fr\) auto/.test(css))
  assert.ok(/\.v2-direct-limit[^}]*font-size: 11px/.test(css))
  assert.ok(/\.v2-copilot textarea[^}]*font-size: 12px/.test(css))
  assert.ok(/\.v2-battle-compose textarea[^}]*font-size: 12px/.test(css))
  assert.ok(/\.v2-battle-panel > p[^}]*font-size: 12px/.test(css))
})

test('busy chats use compact accessible Stop icons without changing cancellation handlers', () => {
  const chat = readFileSync(new URL('../src/components/CopilotExperience.tsx', import.meta.url), 'utf8')
  const battle = readFileSync(new URL('../src/components/IntelligenceExperience.tsx', import.meta.url), 'utf8')
  const css = readFileSync(new URL('../src/ui-shared/styles/app.css', import.meta.url), 'utf8')
  assert.match(chat, /busy && onCancel && <button className="v2-chat-stop"[^>]*title="Stop response" aria-label="Stop response" onClick=\{onCancel\}><Square/)
  assert.match(battle, /busy && <button className="v2-chat-stop"[^>]*title="Stop comparison" aria-label="Stop comparison" onClick=\{\(\) => conversations\.cancel\('battle'\)\}><Square/)
  for (const source of [chat, battle]) {
    assert.match(source, /<Square size=\{15\} fill="currentColor" aria-hidden="true" \/>/)
    assert.doesNotMatch(source, />Cancel(?: comparison)?<\/button>/)
  }
  assert.match(css, /\.v2-copilot > footer \.v2-chat-stop,[^}]*width: 40px; height: 40px; padding: 0/)
  assert.match(css, /\.v2-chat-stop:focus-visible[^}]*outline: 2px solid var\(--cp-accent\)/)
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
