import { useMemo, useState } from 'react'
import { readAnswerDatasets, answerVisualizations, formatEvidenceCell, operatorDatasets, showRequestedCharts } from '../services/copilot/answerPresentation'
import type { AgentVisualization } from '../services/assistantStream'
import type { AgentStep } from '../services/agentSteps'
import { presentSourceRows } from '../services/copilot/sourcePresentation'
import { AgentVisualizationView } from './AgentVisualizationView'

export function AnswerDashboard({ text, question = '', visualizations = [], steps = [], receiptPrefix, checkedText = false }: {
  text: string; question?: string; visualizations?: AgentVisualization[]; steps?: AgentStep[]; receiptPrefix?: string; checkedText?: boolean
}) {
  const source = useMemo(() => presentSourceRows(steps, question), [steps, question])
  const { datasets, issues } = useMemo(() => {
    if (source.hasSourceResults) {
      if (!checkedText || source.invalidated) return source
      const checked = readAnswerDatasets(text.replace(/^[ \t]*>.*$/gm, ''))
      return { ...source, datasets: [
        ...checked.datasets.filter(dataset => !source.datasets.some(existing => existing.csv === dataset.csv)),
        ...source.datasets,
      ], issues: [...source.issues, ...checked.issues] }
    }
    const result = readAnswerDatasets(visualizations.length ? '' : text)
    if (visualizations.length) {
      for (const spec of visualizations) {
        const parsed = readAnswerDatasets(`### ${spec.title}\n\`\`\`csv\n${spec.inlineCsvData}\n\`\`\``)
        for (const dataset of parsed.datasets) {
          if (!result.datasets.some(existing => existing.csv === dataset.csv)) result.datasets.push(dataset)
        }
        result.issues.push(...parsed.issues)
      }
    }
    return result
  }, [text, visualizations, source, checkedText])
  const requested = showRequestedCharts(question)
  const [view, setView] = useState<'table' | 'chart'>('chart')
  const usingSource = source.hasSourceResults
  const charts = usingSource ? [
    ...source.visualizations,
    ...(checkedText && !source.invalidated ? answerVisualizations(datasets.filter(dataset => dataset.sourceStep === undefined), question) : []),
  ] : answerVisualizations(datasets, question)
  if (!requested && !visualizations.length && !issues.length && !datasets.length) return null
  // Explicit chart datasets take precedence over unrelated numeric table columns.
  const explicit = source.invalidated ? [] : visualizations
  const availableSpecs = !requested ? [] : [...explicit, ...(!explicit.length ? charts : []).filter(chart => !explicit.some(spec =>
    spec.xColumn === chart.xColumn && spec.groupBy === chart.groupBy
    && JSON.stringify(spec.yColumns) === JSON.stringify(chart.yColumns) && spec.inlineCsvData === chart.inlineCsvData))]
  const specs = availableSpecs.slice(0, 12)
  const showCharts = view === 'chart' && specs.length > 0
  const findings = operatorDatasets(datasets)
  const auditDatasets = datasets.filter(dataset => !findings.some(finding =>
    finding.title === dataset.title && JSON.stringify(finding.columns) === JSON.stringify(dataset.columns)
    && JSON.stringify(finding.rows) === JSON.stringify(dataset.rows)))
  const renderTable = (dataset: typeof datasets[number], index: number) => <div className="v2-answer-table" key={index}>
    <strong>{dataset.title}</strong><small> {dataset.rows.length} returned rows</small>
    {dataset.sourceStep !== undefined && receiptPrefix
      ? <a className="v2-answer-citation" href={`#${receiptPrefix}-source-${dataset.sourceStep}`} onClick={() => {
        const target = document.getElementById(`${receiptPrefix}-source-${dataset.sourceStep}`)
        for (let parent = target?.parentElement; parent; parent = parent.parentElement) {
          if (parent instanceof HTMLDetailsElement) parent.open = true
        }
      }}>Source {dataset.sourceStep + 1}: {steps[dataset.sourceStep]?.tool}</a>
      : <small className="v2-answer-citation">{dataset.sourceStep !== undefined ? `Source: ${steps[dataset.sourceStep]?.tool}` : checkedText ? 'Source: application-checked findings; causation is not established.' : 'Source: agent response; not independently verified'}</small>}
    <table><thead><tr>{dataset.columns.map(column => <th scope="col" key={column}>{column}</th>)}</tr></thead>
      <tbody>{dataset.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} title={cell}>{formatEvidenceCell(cell, dataset.columns[cellIndex])}</td>)}</tr>)}</tbody>
    </table>
  </div>
  return <section className="v2-answer-dashboard" aria-label="Answer evidence dashboard">
    {issues.map(issue => <p role="alert" key={issue}>{issue}</p>)}
    {availableSpecs.length > 12 && <p role="status">Chart display is limited to 12 panels. All returned table rows remain available.</p>}
    {source.hasSourceResults && <details className="v2-answer-support"><summary>Source timing and coverage</summary><p>{source.summary.split('\n\n').filter(line => !source.issues.includes(line)).join('\n\n')}</p></details>}
    {requested && <div className="v2-chart-tabs" role="group" aria-label="Evidence view">
      <button type="button" className={!showCharts ? 'on' : ''} aria-pressed={!showCharts} onClick={() => setView('table')}>Table only</button>
      <button type="button" className={showCharts ? 'on' : ''} aria-pressed={showCharts} disabled={!specs.length} onClick={() => setView('chart')}>Table + charts</button>
    </div>}
    {findings.map(renderTable)}
    {showCharts && <div className="v2-answer-chart-grid">{specs.map((spec, index) => <AgentVisualizationView spec={spec} key={index} />)}</div>}
    {!specs.length && requested && <p role="status">{datasets.length ? 'The returned rows are shown as a table; they do not contain a complete numeric series to plot.' : 'No structured source dataset was returned for a chart or table.'} Missing values are not plotted as zero.</p>}
    {datasets.length > 0 && <details className="v2-answer-raw"><summary>Evidence audit — complete source tables and raw data</summary>
      {auditDatasets.map(renderTable)}
      <details><summary>Raw evidence CSV ({datasets.reduce((count, dataset) => count + dataset.rows.length, 0)} rows)</summary>
      {datasets.map((dataset, index) => <div key={index}><strong>{dataset.title}</strong><pre>{dataset.csv}</pre></div>)}
      </details>
    </details>}
    {!source.hasSourceResults && issues.length > 0 && <details className="v2-answer-raw"><summary>Unparsed agent output</summary><pre>{text}</pre></details>}
  </section>
}
