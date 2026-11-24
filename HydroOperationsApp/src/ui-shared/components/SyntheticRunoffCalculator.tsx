import { useState, type FormEvent } from 'react'
import { calculateRainfallRunoff, syntheticRunoffScenario } from '../syntheticRunoff'

const example = syntheticRunoffScenario()
type RunoffResult = ReturnType<typeof calculateRainfallRunoff>
const number = (value: number, digits = 2) => value.toLocaleString(undefined, { maximumFractionDigits: digits })

export function SyntheticRunoffCalculator() {
  const [area, setArea] = useState(String(example.areaKm2))
  const [coefficient, setCoefficient] = useState(String(example.coefficient))
  const [rainfall, setRainfall] = useState(example.rainfallMm.map(String))
  const [result, setResult] = useState<RunoffResult | undefined>(() => calculateRainfallRunoff(example.areaKm2, example.coefficient, example.rainfallMm))
  const [error, setError] = useState<string>()
  const invalidate = () => { setResult(undefined); setError(undefined) }
  const reset = () => {
    setArea(String(example.areaKm2))
    setCoefficient(String(example.coefficient))
    setRainfall(example.rainfallMm.map(String))
    setError(undefined)
    setResult(calculateRainfallRunoff(example.areaKm2, example.coefficient, example.rainfallMm))
  }
  const calculate = (event: FormEvent) => {
    event.preventDefault()
    invalidate()
    try {
      if ([area, coefficient, ...rainfall].some(value => !value.trim())) throw new Error('Enter an area, runoff coefficient and rainfall for every day. Use 0 for a dry day.')
      setResult(calculateRainfallRunoff(Number(area), Number(coefficient), rainfall.map(Number)))
    } catch (reason) {
      console.error('Synthetic runoff calculation failed.', reason)
      setError(reason instanceof Error ? reason.message : 'Synthetic runoff calculation failed.')
    }
  }
  return <section className="terrain-runoff terrain-metadata" aria-labelledby="synthetic-runoff-heading">
    <span className="v2-eyebrow">Synthetic demonstration only</span>
    <h2 id="synthetic-runoff-heading">Rainfall-to-runoff calculator</h2>
    <p className="terrain-notice">All defaults are invented, including the catchment area and seven-day rainfall series. This is not a forecast, measured inflow or a calibrated hydrological model. No values come from the imported terrain, Kartverket sample, weather service or Fabric sources.</p>
    <p className="terrain-help">Assumes uniform rainfall and a constant fraction becoming runoff in the same day. Ignores snowmelt, evaporation, soil and groundwater storage, baseflow, travel time and reservoir operations. Daily average flow is not peak discharge. Do not use for operational decisions.</p>
    <form className="terrain-runoff-form" onSubmit={calculate} noValidate aria-label="Calculate synthetic rainfall runoff">
      <div className="terrain-runoff-inputs">
        <label>Assumed catchment area (km²)<input type="number" step="any" min="0" value={area} onChange={event => { invalidate(); setArea(event.target.value) }} /></label>
        <label>Runoff coefficient (0–1)<input type="number" step="any" min="0" max="1" value={coefficient} onChange={event => { invalidate(); setCoefficient(event.target.value) }} /></label>
      </div>
      <fieldset><legend>Synthetic rainfall (mm per day)</legend><div className="terrain-runoff-inputs">{rainfall.map((value, index) => <label key={index}>Day {index + 1}<input type="number" step="any" min="0" value={value} onChange={event => { invalidate(); setRainfall(previous => previous.map((entry, day) => day === index ? event.target.value : entry)) }} /></label>)}</div></fieldset>
      <div className="terrain-import-actions"><button type="submit">Calculate synthetic runoff</button><button type="button" onClick={reset}>Reset synthetic example</button></div>
    </form>
    <p className="terrain-help">Daily volume (m³) = rainfall (mm) × area (km²) × 1,000 × coefficient. Daily average flow (m³/s) = volume ÷ 86,400.</p>
    {error && <p className="terrain-error" role="alert">{error}</p>}
    {!result && !error && <p role="status">Inputs changed. Calculate to show updated demonstration results.</p>}
    {result && <div className="terrain-runoff-results" role="status" aria-label="Synthetic runoff results">
      <dl>
        <div><dt>Synthetic seven-day rainfall</dt><dd>{number(result.rainfallTotalMm)} mm</dd></div>
        <div><dt>Demonstration runoff volume</dt><dd>{number(result.volumeTotalM3)} m³</dd></div>
        <div><dt>Seven-day average runoff flow</dt><dd>{number(result.meanFlowM3s, 3)} m³/s</dd></div>
      </dl>
      <div className="terrain-runoff-table"><table><caption>Synthetic daily runoff estimates (not a forecast)</caption><thead><tr><th scope="col">Day</th><th scope="col">Rainfall (mm)</th><th scope="col">Runoff (mm)</th><th scope="col">Volume (m³)</th><th scope="col">Average flow (m³/s)</th></tr></thead><tbody>{result.days.map(day => <tr key={day.day}><th scope="row">Day {day.day}</th><td>{number(day.rainfallMm)}</td><td>{number(day.runoffMm)}</td><td>{number(day.volumeM3)}</td><td>{number(day.meanFlowM3s, 3)}</td></tr>)}</tbody></table></div>
    </div>}
  </section>
}
