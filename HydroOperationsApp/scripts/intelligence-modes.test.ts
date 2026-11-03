import assert from 'node:assert/strict'
import test from 'node:test'
import { IntelligenceConversations, INTELLIGENCE_MODES, resolveVisibleIntelligenceMode, routeIntelligenceRequest, visibleIntelligenceModes, type IntelligenceLane } from '../src/services/intelligenceModes.ts'

test('visibility controls allow any subset, prefer the available team and fail closed when all modes are hidden', () => {
  for (let mask = 0; mask < 8; mask++) {
    const visibility = { direct: Boolean(mask & 1), multi: Boolean(mask & 2), battle: Boolean(mask & 4) }
    const visible = visibleIntelligenceModes(visibility)
    assert.equal(visible.length, Number(visibility.direct) + Number(visibility.multi) + Number(visibility.battle))
    for (const selected of ['direct', 'multi', 'battle'] as const) {
      const resolved = resolveVisibleIntelligenceMode(selected, visibility)
      if (!visible.length) assert.equal(resolved, undefined)
      else if (visibility[selected]) assert.equal(resolved, selected)
      else assert.equal(resolved, visibility.multi ? 'multi' : visible[0].id)
    }
  }
})

test('mode requests route to independent lanes/sessions, preserving the identical operator question', async () => {
  const calls: Array<{ lane: IntelligenceLane; session: string; question: string; transport: string }> = []
  const controller = new IntelligenceConversations(routeIntelligenceRequest(
    async (question, context) => {
      calls.push({ question, lane: context.lane, session: context.session, transport: 'direct' })
      return { text: `${context.lane} native answer` }
    },
    async (question, context) => {
      calls.push({ question, lane: context.lane, session: context.session, transport: 'hosted' })
      return { text: `${context.lane} native answer`, proposals: [{ id: 'draft' }] as never }
    },
  ))
  const question = '  Same exact operator question\r\nwith actions.  '
  await controller.send('direct', question)
  await controller.send('multi', question)
  await controller.send('battle', question)
  assert.deepEqual(calls.map(call => call.lane), ['direct', 'multi', 'battleDirect', 'battleMulti'])
  assert.deepEqual(calls.map(call => call.transport), ['direct', 'hosted', 'direct', 'hosted'])
  assert.ok(calls.every(call => call.question === question))
  assert.equal(new Set(calls.map(call => call.session)).size, 4)
  const state = controller.getSnapshot()
  for (const lane of ['direct', 'multi', 'battleDirect', 'battleMulti'] as const) {
    assert.equal(state[lane].messages[0].text, question)
    assert.equal(state[lane].messages[1].text, `${lane} native answer`)
    assert.ok(state[lane].messages[1].meta!.elapsedMs >= 0)
    assert.equal(state[lane].messages[1].readOnly, lane !== 'multi')
    assert.equal(Boolean(state[lane].messages[1].proposals?.length), lane === 'multi')
  }
  await controller.send('battle', 'follow-up')
  assert.equal(calls[4].session, calls[2].session)
  assert.equal(calls[5].session, calls[3].session)
  assert.deepEqual(INTELLIGENCE_MODES.map(mode => mode.label), ['Fabric IQ', 'Multi Agent', 'Battle of the Agents'])
})

test('a failed Battle lane never hides or replaces the other answer', async () => {
  const controller = new IntelligenceConversations(async (_question, context) => {
    if (context.lane === 'battleDirect') {
      context.progress('stale partial')
      throw new Error('Direct browser CORS blocked')
    }
    return { text: 'Full governed answer and sources' }
  })
  await controller.send('battle', 'question')
  const state = controller.getSnapshot()
  assert.equal(state.battleDirect.messages[1].failed, true)
  assert.equal(state.battleDirect.messages[1].text, 'Direct browser CORS blocked')
  assert.equal(state.battleMulti.messages[1].text, 'Full governed answer and sources')
  assert.equal(state.multi.messages.length, 0)
  assert.equal(state.direct.messages.length, 0)
})

test('duplicate submissions and tab switching cannot launch duplicate requests', async () => {
  let complete!: () => void
  let calls = 0
  const pending = new Promise<void>(resolve => { complete = resolve })
  const controller = new IntelligenceConversations(async () => { calls++; await pending; return { text: 'answer' } })
  const first = controller.send('direct', 'question')
  await controller.send('direct', 'duplicate')
  controller.getSnapshot()
  controller.subscribe(() => {})()
  assert.equal(calls, 1)
  const other = controller.send('multi', 'independent question')
  assert.equal(calls, 2)
  complete()
  await Promise.all([first, other])
})

test('identity changes clear all histories, abort active work, release approvals and prevent late answer paints', async () => {
  let complete!: () => void
  let signal!: AbortSignal
  const released: string[] = []
  const pending = new Promise<void>(resolve => { complete = resolve })
  const controller = new IntelligenceConversations(async (_question, context) => {
    signal = context.signal
    await pending
    return { text: 'old source answer' }
  }, session => released.push(session))
  controller.setIdentity('workspace/source/user-1')
  const first = controller.send('multi', 'question')
  controller.setIdentity('workspace/source/user-2')
  assert.equal(signal.aborted, true)
  assert.equal(released.length, 1)
  complete()
  await first
  assert.ok(Object.values(controller.getSnapshot()).every(lane => !lane.busy && !lane.messages.length))
})

test('reset and cancellation are mode-local and cannot promote a partial response to success', async () => {
  const controller = new IntelligenceConversations(async (_question, context) => {
    if (context.lane === 'direct') return { text: 'direct history' }
    await new Promise<void>(resolve => context.signal.addEventListener('abort', () => resolve(), { once: true }))
    return { text: 'late cancelled answer' }
  })
  await controller.send('direct', 'direct')
  const pending = controller.send('multi', 'pending')
  controller.cancel('multi')
  await pending
  const state = controller.getSnapshot()
  assert.match(state.multi.messages[1].text, /cancelled or timed out/)
  assert.equal(state.multi.messages[1].failed, true)
  assert.equal(state.direct.messages[1].text, 'direct history')
  controller.reset('multi')
  assert.equal(controller.getSnapshot().multi.messages.length, 0)
  assert.equal(controller.getSnapshot().direct.messages.length, 2)
})
