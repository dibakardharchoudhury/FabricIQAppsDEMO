import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { calculateRainfallRunoff, syntheticRunoffScenario } from '../src/ui-shared/syntheticRunoff.ts'
import { SyntheticRunoffCalculator } from '../src/ui-shared/components/SyntheticRunoffCalculator.tsx'

test('rainfall units yield volume and daily average flow, not peak discharge', () => {
  const result = calculateRainfallRunoff(10, 0.5, [10])
  assert.equal(result.volumeTotalM3, 50_000)
  assert.equal(result.days[0].runoffMm, 5)
  assert.ok(Math.abs(result.meanFlowM3s - 0.5787037037037037) < 1e-12)
})

test('synthetic seven-day scenario conserves runoff volume and includes dry days', () => {
  const scenario = syntheticRunoffScenario()
  const result = calculateRainfallRunoff(scenario.areaKm2, scenario.coefficient, scenario.rainfallMm)
  assert.equal(result.days.length, 7)
  assert.equal(result.rainfallTotalMm, 54)
  assert.equal(result.volumeTotalM3, 270_000)
  assert.equal(result.days[0].meanFlowM3s, 0)
  assert.equal(result.meanFlowM3s * 7 * 86_400, result.volumeTotalM3)
  assert.equal(calculateRainfallRunoff(10, 0, [20, 0]).volumeTotalM3, 0)
  assert.equal(calculateRainfallRunoff(1, 1, [1]).volumeTotalM3, 1000)
})

test('invalid inputs and overflow fail explicitly rather than producing plausible results', () => {
  for (const area of [0, -1, NaN, Infinity]) assert.throws(() => calculateRainfallRunoff(area, 0.5, [10]), /area/)
  for (const coefficient of [-0.01, 1.01, NaN, Infinity]) assert.throws(() => calculateRainfallRunoff(10, coefficient, [10]), /coefficient/)
  for (const rainfall of [[], [-1], [NaN], [Infinity]]) assert.throws(() => calculateRainfallRunoff(10, 0.5, rainfall), /rainfall/)
  assert.throws(() => calculateRainfallRunoff(Number.MAX_VALUE, 1, [10]), /numeric range/)
})

test('calculator exposes editable synthetic inputs, units, limitations and results without terrain or live sources', () => {
  const html = renderToStaticMarkup(createElement(SyntheticRunoffCalculator))
  assert.match(html, /Synthetic demonstration only/)
  assert.match(html, /not a forecast/)
  assert.match(html, /No values come from the imported terrain/)
  assert.match(html, /snowmelt/)
  assert.match(html, /Calculate synthetic runoff/)
  assert.match(html, /270,000/)
  assert.equal((html.match(/type="number"/g) ?? []).length, 9)
})
