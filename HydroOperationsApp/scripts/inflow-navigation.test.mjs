import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

for (const layout of ['v1', 'v2']) {
  test(`inflow model is accessible from the ${layout} navigation`, async () => {
    const navigation = await readFile(new URL(`../src/ui-${layout}/navigation.tsx`, import.meta.url), 'utf8')
    assert.match(navigation, /id: 'inflow-model', label: 'Inflow Model'/)
    assert.match(navigation, /import\('\.\.\/ui-shared\/pages\/InflowModelPage'\)/)
  })
}

test('shared inflow page keeps unavailable model outputs explicit', async () => {
  const page = await readFile(new URL('../src/ui-shared/pages/InflowModelPage.tsx', import.meta.url), 'utf8')
  assert.match(page, /no operational forecast is available/)
  assert.match(page, /No basin-calibrated model output is available/)
  assert.match(page, /https:\/\/www\.varsom\.no\//)
  assert.match(page, /import '\.\.\/styles\/inflow-model\.css'/)
  await readFile(new URL('../src/ui-shared/styles/inflow-model.css', import.meta.url), 'utf8')
})
