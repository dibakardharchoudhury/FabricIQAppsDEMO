import assert from 'node:assert/strict'
import test from 'node:test'
import { analyticalEvidencePlan, chatIntent, compileOrchestrationPlan, handle } from './local-fabric-sources.mjs'

const prompt = 'Which turbines had BAD telemetry quality today, and what work is already open for these?'

test('ordinary quality/work questions never acquire a hidden native dependency based on exact wording', () => {
  for (const question of [prompt, `Please ${prompt}`, `${prompt} Based on this data, do a RCA and tell me which WOs need to be created and help me with the drafts.`]) {
    const intent = chatIntent(question)
    assert.deepEqual(intent.native_sources, [])
    assert.ok(intent.required_specialists.includes('qa'))
    assert.ok(intent.required_evidence_kinds.includes('signal_quality'))
    assert.ok(intent.required_evidence_kinds.includes('work_orders'))
    const reads = analyticalEvidencePlan(question, intent.required_evidence_kinds)
    assert.equal(reads.find(read => read.tool === 'query_signal_quality_snapshot')?.arguments.quality_match, 'any')
    const plan = compileOrchestrationPlan(question, intent)
    assert.ok(plan.steps.every(step => step.specialist !== 'fabric-iq'))
    assert.ok(plan.steps.some(step => step.specialist === 'qa'))
    if (/RCA/.test(question)) {
      const roles = plan.steps.map(step => step.specialist)
      assert.ok(roles.indexOf('qa') < roles.indexOf('rca'))
      assert.ok(roles.indexOf('rca') < roles.indexOf('work-order'))
    }
  }
})

test('current quality requests do not inherit historical matching from unrelated wording', () => {
  const reads = analyticalEvidencePlan('Which turbines have latest BAD quality today and any open work?', ['signal_quality'])
  assert.equal(reads[0].arguments.quality_match ?? 'latest', 'latest')
})

test('an explicitly requested Data Agent stays native rather than silently switching to direct reads', () => {
  const question = `Use the published Data Agent. ${prompt}`
  const intent = chatIntent(question)
  assert.deepEqual(intent.native_sources, ['data-agent'])
  const plan = compileOrchestrationPlan(question, intent)
  assert.equal(plan.steps[0].specialist, 'fabric-iq')
  assert.equal(plan.steps[0].native_source, 'data-agent')
})

test('native plus independent direct verification retains both sources without the specialized joined-bundle contract', async () => {
  const question = `Use the published Data Agent. ${prompt} Independently verify against direct sources.`
  const intent = chatIntent(question)
  assert.deepEqual(intent.native_sources, ['data-agent'])
  assert.ok(intent.required_specialists.includes('qa'))
  for (const kind of ['native_claims', 'signal_quality', 'work_orders']) {
    assert.ok(intent.required_evidence_kinds.includes(kind))
  }
  const plan = compileOrchestrationPlan(question, intent)
  assert.deepEqual(plan.steps.map(step => step.specialist), ['fabric-iq', 'qa'])
  assert.doesNotMatch(plan.steps[0].question, /Return only one JSON object/)
  const result = { rows: [{ equipment_id: 'T1', claim: 'native source claim, not SQL coverage' }] }
  const validated = await handle({ action: 'validate_quality_work_evidence', question, result,
    requested_at_utc: '2026-10-26T12:00:00.000Z' })
  assert.equal(validated.required, false)
  assert.deepEqual(validated.result, result)
})
