import { useMemo, useState } from 'react'
import { readAnswerDatasets, answerVisualizations, formatEvidenceCell } from '../services/copilot/answerPresentation'
import type { AgentVisualization } from '../services/assistantStream'
import type { AgentStep } from '../services/agentSteps'
import { presentSourceRows } from '../services/copilot/sourcePresentation'
import { AgentVisualizationView } from './AgentVisualizationView'

export function AnswerDashboard({ text, question = '', visualizations = [], steps = [] }: {
  text: string; question?: string; visualizations?: AgentVisualization[]; steps?: AgentStep[]
}) {
  const source = useMemo(() => presentSourceRows(steps, question), [steps, question])
  const { datasets, issues } = useMemo(() => {
    if (source.hasSourceResults && (!visualizations.length || source.invalidated)) return source
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
  }, [text, visualizations, source])
  const requested = /\b(chart|plot|graph|dashboard|visuali[sz]e|trend)\b/i.test(question)
  const [view, setView] = useState<'table' | 'chart'>(() => requested || visualizations.length ? 'chart' : 'table')
  const usingSource = source.hasSourceResults && (!visualizations.length || source.invalidated)
  const charts = usingSource ? source.visualizations : answerVisualizations(datasets, question)
  if (!requested && !visualizations.length && !issues.length && !datasets.some(dataset => dataset.format === 'csv')) return null
  // Explicit chart datasets take precedence over unrelated numeric table columns.
  const specs = visualizations.length && !source.invalidated ? visualizations : charts
  const showCharts = view === 'chart' && specs.length > 0
  return <section className="v2-answer-dashboard" aria-label="Answer evidence dashboard">
    {source.hasSourceResults && <p className="v2-answer-source-summary">{source.summary}</p>}
    {!usingSource && issues.map(issue => <p role="alert" key={issue}>{issue}</p>)}
    <div className="v2-chart-tabs" role="group" aria-label="Evidence view">
      <button type="button" className={!showCharts ? 'on' : ''} aria-pressed={!showCharts} onClick={() => setView('table')}>Table</button>
      <button type="button" className={showCharts ? 'on' : ''} aria-pressed={showCharts} disabled={!specs.length} onClick={() => setView('chart')}>Charts</button>
    </div>
    {!showCharts ? datasets.map((dataset, index) => <div className="v2-answer-table" key={index}>
      <strong>{dataset.title}</strong><small> {dataset.rows.length} returned rows</small>
      <table><thead><tr>{dataset.columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>{dataset.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} title={cell}>{formatEvidenceCell(cell, dataset.columns[cellIndex])}</td>)}</tr>)}</tbody>
      </table>
    </div>) : specs.map((spec, index) => <AgentVisualizationView spec={spec} key={index} />)}
    {!specs.length && requested && <p role="status">{datasets.length ? 'The returned rows are shown as a table; they do not contain a complete numeric series to plot.' : 'No structured source dataset was returned for a chart or table.'} Missing values are not plotted as zero.</p>}
    {datasets.length > 0 && <details className="v2-answer-raw"><summary>Raw evidence CSV ({datasets.reduce((count, dataset) => count + dataset.rows.length, 0)} rows)</summary>
      {datasets.map((dataset, index) => <div key={index}><strong>{dataset.title}</strong><pre>{dataset.csv}</pre></div>)}
    </details>}
  </section>
}
