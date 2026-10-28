import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
import { createOntologyCache, OntologyCacheInvalidatedError } from '../src/services/ontologyCache.ts'

const rayfin = readFileSync(new URL('../src/services/rayfin.ts', import.meta.url), 'utf8')
const fabric = readFileSync(new URL('../src/services/fabric.ts', import.meta.url), 'utf8')
const page = readFileSync(new URL('../src/ui-shared/pages/KnowledgeGraphPage.tsx', import.meta.url), 'utf8')
const parse = (source: string) => ts.createSourceFile('test.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const js = (source: string) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

test('choosing an asset in the tree exits fit-all scope and anchors its native neighborhood', () => {
  let action: ts.ArrowFunction | undefined
  const find = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText() === 'selectAsset'
      && node.initializer && ts.isArrowFunction(node.initializer)) action = node.initializer
    ts.forEachChild(node, find)
  }
  find(parse(page))
  assert.ok(action)
  const calls: unknown[] = []
  const select = new Function('graph', 'data', 'setScope', 'setSelection',
    js(`return (${action.getText()});`))(
    { nodes: [{ id: 'equipment:E1', type: 'equipment', entityId: 'E1' }] },
    { actions: { selectAsset: (...args: string[]) => calls.push(args) } },
    (scope: string) => calls.push(scope),
    (selection: unknown) => calls.push(selection),
  ) as (facility: string, asset: string) => void
  select('F1', 'E1')
  assert.deepEqual(calls, [['F1', 'E1'], 'asset', { nodeId: 'equipment:E1', assetId: 'E1' }])
})

function harness() {
  const window = Object.assign(new EventTarget(), {
    setInterval: (_callback: () => void, _delay: number) => 1,
    clearInterval: (_id: number) => undefined,
  })
  const document = Object.assign(new EventTarget(), { hidden: false })
  const sourceCache = createOntologyCache<string[]>(30_000)
  const nativeCache = createOntologyCache<string[]>(300_000)
  const names = new Set(['subscribeWorkOrdersChanged', 'notifyWorkOrdersChanged', 'createWorkOrder', 'updateWorkOrderStatus', 'deleteWorkOrder'])
  const functions = parse(rayfin).statements.filter(node => ts.isFunctionDeclaration(node) && node.name && names.has(node.name.text))
    .map(node => node.getText().replace(/^export /, '')).join('\n')
  assert.equal(functions.match(/^function |^async function /gm)?.length, 5)
  let rows: string[] = []
  let fail = false
  const mutation = async () => {
    if (fail) throw new Error('SQL write rejected')
  }
  const client = { connectors: { hydrooperations: { WorkOrder: {
    create: async () => { await mutation(); rows = [...rows, 'W1:Draft']; return { id: 'W1' } },
    update: async () => { await mutation(); rows = ['W1:Completed'] },
    delete: async () => { await mutation(); rows = [] },
  } } } }
  const api = new Function('window', 'Event', 'client', 'crypto', js(`
    const WORK_ORDERS_CHANGED_EVENT = 'hydro:work-orders-changed';
    ${functions}
    return { subscribeWorkOrdersChanged, createWorkOrder, updateWorkOrderStatus, deleteWorkOrder };
  `))(window, Event, client, crypto) as {
    subscribeWorkOrdersChanged: (listener: () => void) => () => void
    createWorkOrder: (user: { id: string }, equipment: string) => Promise<unknown>
    updateWorkOrderStatus: (id: string, status: string) => Promise<void>
    deleteWorkOrder: (id: string) => Promise<void>
  }
  const invalidation = parse(fabric).statements.find(node => ts.isExpressionStatement(node)
    && ts.isCallExpression(node.expression) && node.expression.expression.getText() === 'subscribeWorkOrdersChanged')
  assert.ok(invalidation, 'service must invalidate cached graphs even while the graph page is unmounted')
  new Function('subscribeWorkOrdersChanged', 'ontologySourceGraphCache', 'ontologyGraphCache', js(invalidation.getText()))(
    api.subscribeWorkOrdersChanged, sourceCache, nativeCache,
  )
  const read = () => sourceCache.read('graph', false, async () => [...rows])
  let displayed: string[] | null = null
  let version = 0
  let reads = 0
  let hold: Promise<void> | undefined
  const loading: Promise<void>[] = []
  const forceReads: boolean[] = []
  const loadOntologyGraph = (force: boolean) => {
    const current = ++version
    reads++
    forceReads.push(force)
    const blocked = hold
    const result = (async () => {
      try {
        const snapshot = await sourceCache.read('graph', force, async () => {
          const value = [...rows]
          await blocked
          return value
        })
        if (current === version) displayed = snapshot
      } catch (error) {
        if (!(error instanceof OntologyCacheInvalidatedError)) throw error
      }
    })()
    loading.push(result)
    return result
  }
  const effects: ts.CallExpression[] = []
  const findEffects = (node: ts.Node) => {
    if (ts.isCallExpression(node) && node.expression.getText() === 'useEffect'
      && node.arguments[0]?.getText().includes('subscribeWorkOrdersChanged')) effects.push(node)
    ts.forEachChild(node, findEffects)
  }
  findEffects(parse(page))
  assert.equal(effects.length, 1, 'mounted graph must subscribe to committed work-order changes')
  const effect = new Function('window', 'document', 'subscribeWorkOrdersChanged', 'loadOntologyGraph', 'cancelGraphQuery',
    'GRAPH_REFRESH_POLL_MS', js(`return (${effects[0].arguments[0].getText()});`))(
    window, document, api.subscribeWorkOrdersChanged, loadOntologyGraph, () => { version++ }, 300_000,
  ) as () => () => void
  const settle = async () => {
    for (let index = 0; index < 8; index++) await Promise.resolve()
    await Promise.all(loading)
    for (let index = 0; index < 8; index++) await Promise.resolve()
  }
  return {
    api, read, sourceCache, nativeCache, effect, settle, forceReads,
    displayed: () => displayed, reads: () => reads,
    failWrites: () => { fail = true },
    blockReads: (promise?: Promise<void>) => { hold = promise },
    visibility: (hidden: boolean) => { document.hidden = hidden; document.dispatchEvent(new Event('visibilitychange')) },
  }
}

test('create, status change and delete notify only after SQL commits and invalidate both graph snapshots', async () => {
  const h = harness()
  let notifications = 0
  const unsubscribe = h.api.subscribeWorkOrdersChanged(() => { notifications++ })
  for (const [write, expected] of [
    [() => h.api.createWorkOrder({ id: 'operator' }, 'E1'), ['W1:Draft']],
    [() => h.api.updateWorkOrderStatus('W1', 'Completed'), ['W1:Completed']],
    [() => h.api.deleteWorkOrder('W1'), []],
  ] as const) {
    await h.read()
    await h.nativeCache.read('native', false, async () => ['old materialization'])
    await write()
    assert.deepEqual(await h.read(), expected, 'returning to graph must not reuse a pre-write snapshot')
    assert.deepEqual(await h.nativeCache.read('native', false, async () => ['new materialization']), ['new materialization'])
  }
  assert.equal(notifications, 3)
  h.failWrites()
  await assert.rejects(h.api.createWorkOrder({ id: 'operator' }, 'E1'), /SQL write rejected/)
  await assert.rejects(h.api.updateWorkOrderStatus('W1', 'Draft'), /SQL write rejected/)
  await assert.rejects(h.api.deleteWorkOrder('W1'), /SQL write rejected/)
  assert.equal(notifications, 3, 'failed writes must not announce committed changes')
  unsubscribe()
})

test('an open graph rereads committed orders without navigation or manual refresh and unsubscribes on unmount', async () => {
  const h = harness()
  const dispose = h.effect()
  await h.settle()
  assert.deepEqual(h.displayed(), [])
  await h.api.createWorkOrder({ id: 'operator' }, 'E1')
  await h.settle()
  assert.deepEqual(h.displayed(), ['W1:Draft'])
  await h.api.updateWorkOrderStatus('W1', 'Completed')
  await h.settle()
  assert.deepEqual(h.displayed(), ['W1:Completed'])
  await h.api.deleteWorkOrder('W1')
  await h.settle()
  assert.deepEqual(h.displayed(), [])
  assert.deepEqual(h.forceReads, [false, true, true, true])
  dispose()
  const reads = h.reads()
  await h.api.createWorkOrder({ id: 'operator' }, 'E1')
  await h.settle()
  assert.equal(h.reads(), reads)
})

test('a write during an in-flight read queues a fresh read and rejects the old snapshot', async () => {
  const h = harness()
  let release!: () => void
  h.blockReads(new Promise<void>(resolve => { release = resolve }))
  const dispose = h.effect()
  await Promise.resolve()
  await h.api.createWorkOrder({ id: 'operator' }, 'E1')
  h.blockReads()
  release()
  await h.settle()
  assert.deepEqual(h.displayed(), ['W1:Draft'])
  assert.deepEqual(h.forceReads, [false, true])
  dispose()
})

test('a hidden graph defers a mutation refresh until visibility resumes', async () => {
  const h = harness()
  const dispose = h.effect()
  await h.settle()
  h.visibility(true)
  await h.api.createWorkOrder({ id: 'operator' }, 'E1')
  await h.settle()
  assert.equal(h.reads(), 1)
  h.visibility(false)
  await h.settle()
  assert.deepEqual(h.displayed(), ['W1:Draft'])
  assert.deepEqual(h.forceReads, [false, true])
  dispose()
})
