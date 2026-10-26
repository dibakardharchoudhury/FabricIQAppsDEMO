type Table = { Columns: Array<{ ColumnName: string }>; Rows: unknown[][] }
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

export function parseKustoPayload(result: unknown): Table {
  if (!record(result) || result.error || (result.HasErrors != null && result.HasErrors !== false)
    || (result.Exceptions != null && (!Array.isArray(result.Exceptions) || result.Exceptions.length))
    || !Array.isArray(result.Tables) || !result.Tables.length) {
    throw new Error('KQL returned an error, partial result or an unexpected result envelope.')
  }
  const tables = result.Tables.map((raw: unknown): Table => {
    if (!record(raw) || !Array.isArray(raw.Columns) || !Array.isArray(raw.Rows)) {
      throw new Error('KQL result is missing columns or rows.')
    }
    const columns = raw.Columns.map((column: unknown) => {
      if (!record(column) || typeof column.ColumnName !== 'string') throw new Error('KQL column is invalid.')
      return { ColumnName: column.ColumnName }
    })
    if (new Set(columns.map(column => column.ColumnName)).size !== columns.length) throw new Error('KQL columns are ambiguous.')
    const rows: unknown[][] = []
    for (const row of raw.Rows) {
      if (!Array.isArray(row) || row.length !== columns.length) throw new Error('KQL row does not match its columns.')
      rows.push(row)
    }
    return { Columns: columns, Rows: rows }
  })
  if (tables.length === 1) return tables[0]
  const objects = (table: Table): Record<string, unknown>[] =>
    table.Rows.map(row => Object.fromEntries(table.Columns.map((column, index) => [column.ColumnName, row[index]])))
  const contents = tables.filter(table => ['Ordinal', 'Kind', 'Name'].every(name => table.Columns.some(c => c.ColumnName === name)))
  if (contents.length !== 1) throw new Error('KQL table-of-contents is missing or ambiguous.')
  const entries = objects(contents[0]).map(entry => {
    if (typeof entry.Ordinal !== 'number' || !Number.isInteger(entry.Ordinal)
      || entry.Ordinal < 0 || entry.Ordinal >= tables.length || tables[entry.Ordinal] === contents[0]
      || typeof entry.Kind !== 'string' || !['QueryResult', 'QueryProperties', 'QueryStatus'].includes(entry.Kind)
      || typeof entry.Name !== 'string') throw new Error('KQL table-of-contents contains invalid entries.')
    return { ordinal: entry.Ordinal, kind: entry.Kind, name: entry.Name }
  })
  if (entries.length !== tables.length - 1 || new Set(entries.map(entry => entry.ordinal)).size !== entries.length) {
    throw new Error('KQL table-of-contents is incomplete or ambiguous.')
  }
  const primary = entries.filter(entry => entry.kind === 'QueryResult' && entry.name === 'PrimaryResult')
  const status = entries.filter(entry => entry.kind === 'QueryStatus')
  if (primary.length !== 1 || status.length !== 1 || entries.filter(entry => entry.kind === 'QueryResult').length !== 1) {
    throw new Error('KQL must return one primary result and one completion status.')
  }
  const statuses = objects(tables[status[0].ordinal])
  if (!statuses.some(row => row.Severity === 4 && row.StatusCode === 0)
    || statuses.some(row => typeof row.Severity !== 'number' || !Number.isInteger(row.Severity)
      || row.Severity < 4 || row.StatusCode !== 0)) {
    throw new Error('KQL completion reports failure, warning or incomplete execution; partial rows rejected.')
  }
  return tables[primary[0].ordinal]
}
