import assert from 'node:assert/strict'
import { readFile, readdir, stat } from 'node:fs/promises'
import test from 'node:test'

const dist = new URL('../dist/', import.meta.url)

test('production entry and eager preloads stay below 900 KB of JavaScript', async () => {
  const html = await readFile(new URL('index.html', dist), 'utf8')
  const scripts = [...html.matchAll(/<(?:script|link)\b[^>]*(?:src|href)="(\/assets\/[^"]+\.js)"/g)]
    .map(match => match[1])
  assert.ok(scripts.length, 'Run npm run build before this production-bundle check.')
  let total = 0
  for (const script of new Set(scripts)) {
    const file = new URL(script.slice(1), dist)
    total += (await stat(file)).size
  }
  assert.ok(total < 900_000, `Initial JavaScript is ${total} bytes; expected less than 900,000.`)
})

test('production map includes Leaflet positioning and clipping styles', async () => {
  const assets = new URL('assets/', dist)
  const files = (await readdir(assets)).filter(name => name.endsWith('.css'))
  const css = (await Promise.all(files.map(name => readFile(new URL(name, assets), 'utf8')))).join('\n')
  assert.match(css, /\.leaflet-container\s*\{[^}]*overflow:\s*hidden/)
  assert.match(css, /\.leaflet-pane[^}]*position:\s*absolute/)
  const component = await readFile(new URL('../src/components/FacilityMap.tsx', import.meta.url), 'utf8')
  assert.match(component, /import\s+['"]leaflet\/dist\/leaflet\.css['"]/)
})
