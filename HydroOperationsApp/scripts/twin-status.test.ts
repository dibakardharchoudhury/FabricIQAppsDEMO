import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import * as React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'
import * as twin from '../src/twin.ts'

const signal: twin.TwinSignal = {
  id: 'power', label: 'power_output', nodeId: 'ns=2;s=T012.power_output',
  value: 326.702, unit: 'MW', quality: 'GOOD', hasOpenIssue: true,
  eventTime: '2026-09-30T19:00:00Z',
}

test('signal quality remains independent of maintenance and reading age', () => {
  for (const [quality, signalExpected, assetExpected] of [
    ['GOOD', 'ok', 'warn'], [' good ', 'ok', 'warn'], ['UNCERTAIN', 'warn', 'warn'], ['BAD', 'crit', 'crit'],
  ] as const) {
    assert.equal(twin.twinSignalStatus({ ...signal, quality }), signalExpected)
    assert.equal(twin.twinStatus({ ...signal, quality }), assetExpected, 'Noncritical maintenance must not be promoted to critical')
  }
  assert.equal(twin.twinStatus({ ...signal, hasCriticalIssue: true }), 'crit')
  assert.equal(twin.twinSignalStatus({ ...signal, value: 0 }), 'ok')
  assert.equal(twin.twinSignalStatus({ ...signal, value: undefined }), 'nodata')
  assert.equal(twin.freshnessOf(signal.eventTime, Date.parse('2026-09-30T19:17:00Z')), 'dead')
  assert.equal(twin.ageLabel(signal.eventTime, Date.parse('2026-09-30T19:17:00Z')), '17m ago')
})

test('Digital Twin hotspot, selected detail and counts agree with current quality while retaining the work order', async () => {
  const source = await readFile(new URL('../src/components/AssetModelViewer.tsx', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  })
  let nullState = 0
  const dependencies: Record<string, unknown> = {
    '@google/model-viewer': {},
    react: {
      ...React,
      useState: (initial: unknown) => React.useState(initial === null
        ? ++nullState === 1 ? { center: { x: 0, y: 0, z: 0 }, dims: { x: 1, y: 1, z: 1 } } : signal.id
        : initial),
    },
    'react/jsx-runtime': await import('react/jsx-runtime'),
    '../twin': twin,
  }
  const exports: { AssetModelViewer?: React.ComponentType<{ model: object; signals: twin.TwinSignal[] }> } = {}
  new Function('require', 'exports', outputText)((name: string) => {
    assert.ok(name in dependencies, `Unexpected viewer dependency: ${name}`)
    return dependencies[name]
  }, exports)
  assert.ok(exports.AssetModelViewer)
  const Viewer = exports.AssetModelViewer
  for (const [quality, css] of [['GOOD', 'ok'], ['UNCERTAIN', 'warn'], ['BAD', 'crit']] as const) {
    const current = { ...signal, quality, value: quality === 'GOOD' ? 326.702 : 327.1, eventTime: new Date().toISOString() }
    function RenderViewer() {
      nullState = 0
      return React.createElement(Viewer, { model: { modelName: 'T012', modelUrl: 'https://example.test/t012.glb' }, signals: [current] })
    }
    const html = renderToStaticMarkup(React.createElement(RenderViewer))
    assert.match(html, new RegExp(`class="twin-hotspot ${css} selected"`))
    assert.match(html, new RegExp(`class="twin-detail-mark ${css}"`))
    assert.match(html, new RegExp(`Quality: ${quality}`))
    assert.match(html, /open work order/)
    assert.match(html, new RegExp(`${current.value} MW`))
    assert.match(html, /Live · just now/)
    assert.doesNotMatch(html, /Bad \/ open order/)
  }
})
