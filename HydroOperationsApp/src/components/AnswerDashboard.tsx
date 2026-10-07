import { useMemo, useState } from 'react'
import { readAnswerDatasets, answerVisualizations } from '../services/copilot/answerPresentation'
import type { AgentVisualization } from '../services/assistantStream'
import { AgentVisualizationView } from './AgentVisualizationView'

export function AnswerDashboard({ text, question = '', visualizations = [] }: {
  text: string; question?: string; visualizations?: AgentVisualization[]
}) {
  const { datasets, issues } = useMemo(() => {
    const result = readAnswerDatasets(text)
    if (!result.datasets.length) {
      for (const spec of visualizations) {
        const parsed = readAnswerDatasets(`### ${spec.title}\n\`\`\`csv\n${spec.inlineCsvData}\n\`\`\``)
        result.datasets.push(...parsed.datasets)
        result.issues.push(...parsed.issues)
      }
    }
    return result
  }, [text, visualizations])
  const [view, setView] = useState<'table' | 'chart'>('chart')
  const requested = /\b(chart|plot|graph|dashboard|visuali[sz]e|trend)\b/i.test(question)
  const charts = answerVisualizations(datasets, question)
  if (!requested && !visualizations.length && !issues.length) return null
  // Explicit chart datasets take precedence over unrelated numeric table columns.
  const specs = charts.length ? charts : visualizations
  return <section className="v2-answer-dashboard" aria-label="Answer evidence dashboard">
    {issues.map(issue => <p role="alert" key={issue}>{issue}</p>)}
    <div className="v2-chart-tabs" role="group" aria-label="Evidence view">
      <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button>
      <button type="button" aria-pressed={view === 'chart'} onClick={() => setView('chart')}>Charts</button>
    </div>
    {view === 'table' ? datasets.map((dataset, index) => <div className="v2-answer-table" key={index}>
      <strong>{dataset.title}</strong><small> {dataset.rows.length} returned rows</small>
      <table><thead><tr>{dataset.columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>{dataset.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell || 'Not supplied'}</td>)}</tr>)}</tbody>
      </table>
    </div>) : specs.map((spec, index) => <AgentVisualizationView spec={spec} key={index} />)}
    {!specs.length && requested && <p role="status">No complete numeric dataset was returned for a chart. The answer above is preserved; missing values are not plotted as zero.</p>}
  </section>
}
