import { useMemo, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowRight, Boxes, Check, ChevronRight, CircleAlert, Database, FileJson,
  GitBranch, Layers3, Link2, LoaderCircle, Plus, Search, ShieldCheck, Sparkles, Upload, X } from 'lucide-react'
import {
  canonicalJson, columnPeers, fingerprint, parseContract, preview, reconcileSources, suggestions, updateMapping, validateContract,
  type Contract, type Entity, type Mapping, type Property, type Samples, type Source, type Transform,
} from './contract'
import { exampleContract, exampleSamples } from './example'
import { ontologyIssues, readOntology } from './ontology'
import { listDrafts, saveDraft, signIn, type Draft } from './store'

const empty: Contract = {
  formatVersion: 1, name: 'Untitled data product', ontology: { workspaceId: '', itemId: '', generation: 2, definitionHash: '' },
  entities: [], sources: [], mappings: [], relationships: [],
}
type Page = 'inbox' | 'structure' | 'preview' | 'releases'
function download(name: string, value: string) {
  const url = URL.createObjectURL(new Blob([value], { type: 'application/json' }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = name; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const message = (error: unknown) => error instanceof Error ? error.message : 'The operation failed.'

export function MasterDataApp() {
  const [contract, setContract] = useState<Contract>(empty)
  const [samples, setSamples] = useState<Samples>({})
  const [example, setExample] = useState(false)
  const [page, setPage] = useState<Page>('inbox')
  const [selection, setSelection] = useState('customer')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'pending' | 'overlap'>('all')
  const [selectedColumn, setSelectedColumn] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [user, setUser] = useState('')
  const [history, setHistory] = useState<Draft[]>([])
  const [cursor, setCursor] = useState<string>()
  const [hasMore, setHasMore] = useState(false)
  const [editor, setEditor] = useState<string | null>(null)
  const [editorError, setEditorError] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const [importKind, setImportKind] = useState<'contract' | 'inventory' | 'samples'>('contract')
  const issues = useMemo(() => validateContract(contract), [contract])
  const simulation = useMemo(() => Object.keys(samples).length ? preview(contract, samples) : null, [contract, samples])
  const entity = contract.entities.find(e => e.id === selection) ?? contract.entities[0]
  const sources = contract.sources.filter(s => s.entity === entity?.id)
  const columns = sources.flatMap(source => source.columns.map(column => {
    const mapping = contract.mappings.find(m => m.source === source.id && m.column === column.name)
    const overlap = Boolean(mapping?.target && contract.mappings.some(m => m.source !== source.id && m.target === mapping.target
      && sources.some(s => s.id === m.source)))
    const potentialOverlap = !overlap && columnPeers(contract, source, column.name).length > 0
    return { source, column, mapping, overlap, potentialOverlap }
  }))
  const visible = columns.filter(({ source, column, mapping, overlap, potentialOverlap }) =>
    `${source.label} ${column.name} ${mapping?.target ?? ''}`.toLowerCase().includes(search.toLowerCase())
    && (filter === 'all' || (filter === 'pending' ? !mapping?.reviewed : overlap || potentialOverlap)))
  const current = visible.find(c => `${c.source.id}.${c.column.name}` === selectedColumn) ?? visible[0]
  const pending = contract.sources.reduce((total, source) => total + source.columns.filter(c =>
    !contract.mappings.some(m => m.source === source.id && m.column === c.name && m.reviewed)).length, 0)
  const overlaps = contract.entities.reduce((total, e) => total + e.properties.filter(p => new Set(
    contract.mappings.filter(m => m.target === p.name && contract.sources.some(s => s.id === m.source && s.entity === e.id))
      .map(m => m.source)).size > 1).length, 0)

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (error) { setError(message(error)) } finally { setBusy(false) }
  }
  function load(value: unknown, isExample = false) {
    const next = parseContract(value)
    setContract(next); setSelection(next.entities[0]?.id ?? ''); setSelectedColumn('')
    setSamples(isExample ? exampleSamples : {}); setExample(isExample); setError(''); setNotice('')
  }
  function chooseImport(kind: typeof importKind) {
    setImportKind(kind); fileInput.current?.click()
  }
  async function importFile(file?: File) {
    if (!file) return
    if (file.size > 2_000_000) throw new Error('Imports are limited to 2 MB. Samples are for bounded simulation, not full source extracts.')
    const value: unknown = JSON.parse(await file.text())
    if (importKind === 'contract') load(value)
    if (importKind === 'inventory') { setContract(reconcileSources(contract, value)); setSamples({}); setNotice('Inventory reconciled. Changed bindings and types need a new review; removed columns remain visible as blockers.') }
    if (importKind === 'samples') {
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.entries(value).some(([id, rows]) =>
        !contract.sources.some(s => s.id === id) || !Array.isArray(rows) || rows.length > 1000
        || rows.some(row => !row || typeof row !== 'object' || Array.isArray(row)
          || Object.values(row).some(v => v !== null && !['string', 'number', 'boolean'].includes(typeof v))))) {
        throw new Error('Samples must be an object keyed by declared source IDs, each containing up to 1,000 flat scalar records.')
      }
      setSamples(value as Samples); setNotice('Samples loaded in memory only. Simulation is not a full-data quality certificate.')
    }
  }
  async function verify() {
    const { live, hash } = await readOntology(contract)
    const problems = ontologyIssues(contract, live)
    if (problems.length) throw new Error(problems.map(p => p.message).join('\n'))
    const bound = { ...contract, ontology: { ...contract.ontology, definitionHash: hash } }
    setContract(bound); setExample(false)
    return bound
  }
  async function release() {
    if (example) throw new Error('Training data cannot be released. Import a real contract and verify its live ontology.')
    const bound = await verify()
    const blockers = validateContract(bound)
    if (blockers.length) throw new Error(blockers.map(p => p.message).join('\n'))
    if (simulation?.conflicts.length) throw new Error('Resolve the loaded sample conflicts or remove the sample. Full-data validation still runs in the pipeline.')
    const hash = await fingerprint(bound)
    download(`master-data-${hash.slice(0, 12)}.json`, canonicalJson(bound))
    setNotice(`Release candidate downloaded. SHA-256: ${hash}. An authorized pipeline operator must pin this digest independently; no Gold data or semantic model has been published.`)
  }
  async function historyPage(append = false) {
    const result = await listDrafts(append ? cursor : undefined)
    setHistory(items => append ? [...items, ...result.items] : result.items)
    if (result.hasNextPage && (!result.endCursor || (append && result.endCursor === cursor))) throw new Error('SQL history pagination did not advance.')
    setCursor(result.endCursor); setHasMore(result.hasNextPage)
  }
  const changeProperty = (property: Property) => setContract(c => ({
    ...c, entities: c.entities.map(e => e.id === entity.id ? { ...e, properties: e.properties.map(p => p.name === property.name ? property : p) } : e),
    mappings: c.mappings.map(m => sources.some(s => s.id === m.source) && m.target === property.name ? { ...m, reviewed: false } : m),
  }))

  return <div className="mdm">
    <aside className="mdm-sidebar">
      <a className="mdm-brand" href="./master-data.html"><span><Layers3 size={24} /></span>concord<span className="mdm-brand-dot">.</span></a>
      <div className="mdm-workspace"><span className="mdm-workspace-icon"><Boxes size={18} /></span><div>Data stewardship<small>MICROSOFT FABRIC</small></div></div>
      <div className="mdm-nav-label">WORKSPACE</div>
      <nav aria-label="Master data navigation">{([
        ['inbox', GitBranch, 'Mapping workbench'], ['structure', Boxes, 'Entities & relationships'],
        ['preview', Database, 'Golden record preview'], ['releases', ShieldCheck, 'Versions & release'],
      ] as const).map(([id, Icon, title]) => <button key={id} className={page === id ? 'active' : ''} onClick={() => setPage(id)}>
        <Icon size={18} /><span>{title}</span>{id === 'inbox' && pending > 0 && <b>{pending}</b>}
      </button>)}</nav>
      <div className="mdm-sidebar-note"><ShieldCheck size={23} /><strong>Governed by design</strong><p>Silver stays untouched.<br />Gold changes only through an approved pipeline contract.</p></div>
      <button className="mdm-profile" disabled={busy} onClick={() => void run(async () => {
        setUser(''); setHistory([]); setCursor(undefined); setHasMore(false)
        const identity = await signIn()
        await historyPage(); setUser(identity)
      })}>
        <span className="mdm-avatar">DS</span><span>{user ? 'Fabric connected' : 'Connect to Fabric'}<small>{user ? 'SQL draft history enabled' : 'Sign in for durable drafts'}</small></span><ChevronRight size={16} />
      </button>
    </aside>
    <div className="mdm-main">
      <header className="mdm-topbar"><span>Data products <ChevronRight size={14} /> <strong>{contract.name}</strong></span>
        <span className={`mdm-environment ${example ? 'training' : ''}`}><i />{example ? 'SYNTHETIC TRAINING' : 'DESIGN WORKSPACE'}</span>
      </header>
      <main>
        <div className="mdm-page-heading">
          <div><div className="mdm-eyebrow">SILVER TO GOLD / MASTER DATA MANAGEMENT</div><h1>{page === 'inbox' ? 'Make your data agree.' : page === 'structure' ? 'One shared understanding.' : page === 'preview' ? 'Every value has a story.' : 'Trust, one version at a time.'}</h1>
            <p>{page === 'inbox' ? 'Resolve overlapping sources. Define the rules. Build a golden dataset everyone can trust.' : page === 'structure' ? 'Follow declared ontology entities, relationships, and downstream dependencies.' : page === 'preview' ? 'Simulate matching and survivorship on a bounded, explicitly supplied sample.' : 'Save durable drafts and prepare an independently approved pipeline release.'}</p>
          </div>
          <div className="mdm-actions"><button onClick={() => chooseImport('contract')} disabled={busy}><Upload size={15} />Import contract</button>
            <button className="primary" disabled={busy || !contract.entities.length} onClick={() => void run(async () => {
              await saveDraft(contract); await historyPage(); setNotice('Immutable draft saved to Fabric SQL and verified by readback. This is not a production approval.')
            })}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}Save draft</button></div>
        </div>
        <input hidden ref={fileInput} type="file" accept=".json,application/json" onChange={event => {
          const file = event.target.files?.[0]; event.target.value = ''; void run(() => importFile(file))
        }} />
        {error && <div className="mdm-banner error" role="alert"><CircleAlert size={18} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}
        {notice && <div className="mdm-banner" role="status"><ShieldCheck size={18} /><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16} /></button></div>}
        {example && <div className="mdm-training"><Sparkles size={16} />Training workspace: fictional CRM and ERP metadata and sample records. No live sources are connected.</div>}
        {!contract.entities.length ? <section className="mdm-empty">
          <span className="mdm-empty-icon"><GitBranch size={36} /></span><div className="mdm-eyebrow">A COMMON LANGUAGE FOR EVERY SOURCE</div>
          <h2>From scattered columns<br />to a shared source of truth.</h2><p>Start with your Silver schema inventory and an ontology mapping contract,<br />or explore how two customer sources become one trusted dataset.</p>
          <div className="mdm-actions"><button className="primary" onClick={() => load(exampleContract(), true)}><Sparkles size={16} />Explore customer example</button>
            <button onClick={() => { setEditor(JSON.stringify(exampleContract(), null, 2)) }}>Create from template<FileJson size={16} /></button></div>
          <div className="mdm-empty-flow"><span><Database />Silver sources</span><ArrowRight /><span><GitBranch />Stewardship rules</span><ArrowRight /><span><ShieldCheck />Golden dataset</span></div>
        </section> : <>
          <section className="mdm-stats" aria-label="Contract metrics">
            {[['Source systems', contract.sources.length, 'Across your Silver layer', Database], ['Canonical entities', contract.entities.length, 'Defined by your ontology', Boxes],
              ['Overlapping attributes', overlaps, 'Explicit survivorship required', Link2], ['Decisions to review', pending, 'Before the next Gold release', CircleAlert]].map(([label, count, detail, Icon]) => {
                const MetricIcon = Icon as typeof Database
                return <div className="mdm-stat" key={String(label)}><span>{String(label)}<MetricIcon size={17} /></span><strong>{String(count)}<small>{String(label) === 'Decisions to review' ? 'needs attention' : ''}</small></strong><p>{String(detail)}</p></div>
              })}
          </section>
          <section className="mdm-pipeline" aria-label="Publication pipeline">
            <span><i className="silver" /><strong>Silver</strong><small>Conformed sources</small></span><ArrowRight size={17} />
            <span className="selected"><GitBranch size={18} /><strong>Stewardship</strong><small>Mapping & merge rules</small></span><ArrowRight size={17} />
            <span><i className="gold" /><strong>Gold</strong><small>Versioned golden records</small></span><ArrowRight size={17} />
            <span><Boxes size={18} /><strong>Semantic model</strong><small>Direct Lake + declared joins</small></span>
            <b className={issues.length ? 'mdm-chip amber' : 'mdm-chip green'}>{issues.length ? `${issues.length} contract blockers` : 'Metadata review complete'}</b>
          </section>
          {page === 'inbox' && <>
            <div className="mdm-section-heading"><div><h2>Mapping workbench</h2><p>Give every source column a place, or explicitly leave it out.</p></div>
              <div className="mdm-actions"><button onClick={() => chooseImport('inventory')}><Upload size={14} />Refresh inventory</button><button onClick={() => setEditor(JSON.stringify(contract, null, 2))}><FileJson size={14} />Edit contract</button></div></div>
            <div className="mdm-workbench">
              <div className="mdm-entity-rail"><div className="mdm-nav-label">ONTOLOGY ENTITIES</div>{contract.entities.map(e => <button key={e.id} className={entity?.id === e.id ? 'active' : ''} onClick={() => { setSelection(e.id); setSelectedColumn('') }}><Boxes size={17} /><span>{e.label}<small>{contract.sources.filter(s => s.entity === e.id).length} source systems</small></span><ChevronRight size={14} /></button>)}
                <div className="mdm-rail-help"><Link2 size={17} /><p>A shared name is a suggestion, not proof of shared meaning or identity.</p></div>
              </div>
              <div className="mdm-column-list">
                <div className="mdm-list-heading"><h3>{entity?.label}<span>{columns.length} source columns</span></h3><div className="mdm-search"><Search size={15} /><input placeholder="Find a source or column..." aria-label="Find source columns" value={search} onChange={e => setSearch(e.target.value)} /></div></div>
                <div className="mdm-filters">{(['all', 'pending', 'overlap'] as const).map(f => <button className={filter === f ? 'active' : ''} key={f} onClick={() => setFilter(f)}>{f === 'all' ? 'All columns' : f === 'pending' ? 'Needs review' : 'Overlapping'}</button>)}</div>
                <div className="mdm-table-scroll"><table className="mdm-table"><thead><tr><th>SOURCE COLUMN</th><th>CANONICAL ATTRIBUTE</th><th>STATUS</th></tr></thead><tbody>
                  {visible.map(({ source, column, mapping, overlap, potentialOverlap }) => <tr key={`${source.id}.${column.name}`} className={current?.source.id === source.id && current.column.name === column.name ? 'active' : ''} onClick={() => setSelectedColumn(`${source.id}.${column.name}`)}>
                    <td><button className="mdm-column-button" onClick={() => setSelectedColumn(`${source.id}.${column.name}`)}><span className="mdm-source-icon"><Database size={15} /></span><span><strong>{column.name}</strong><small>{source.label} <span className="mdm-type">{column.type}</span></small></span></button></td>
                    <td>{mapping?.target ? <span className="mdm-canonical"><ArrowRight size={13} />{mapping.target}{overlap && <Link2 size={12} />}</span> : potentialOverlap
                      ? <span className="mdm-chip amber"><Link2 size={12} />Potential overlap</span>
                      : <span className="mdm-muted">{mapping ? 'Excluded with rationale' : 'No mapping yet'}</span>}</td>
                    <td><span className={`mdm-chip ${mapping?.reviewed ? 'green' : 'amber'}`}>{mapping?.reviewed ? <Check size={11} /> : <CircleAlert size={11} />}{mapping?.reviewed ? 'Reviewed' : mapping ? 'Review' : 'New column'}</span></td>
                  </tr>)}
                </tbody></table>{!visible.length && <p className="mdm-no-results">No columns match this filter.</p>}</div>
                <div className="mdm-table-footer"><ShieldCheck size={14} />Nothing is merged by column name alone.</div>
              </div>
              {current && entity && <MappingPanel key={`${entity.id}.${current.source.id}.${current.column.name}.${JSON.stringify(current.mapping)}`}
                entity={entity} source={current.source} column={current.column.name} contract={contract}
                onAdd={property => {
                  setContract(c => ({ ...c, entities: c.entities.map(e => e.id === entity.id ? { ...e, properties: [...e.properties, property] } : e) }))
                  setNotice(`Canonical attribute ${property.name} added to this draft. It must exist in the live ontology before release.`)
                }}
                existing={current.mapping} onSave={mapping => {
                  setContract(c => updateMapping(c, mapping)); setNotice(`Decision recorded for ${mapping.source}.${mapping.column}. Save a draft to persist it.`)
                }} />}
            </div>
            {entity && <section className="mdm-rules"><div className="mdm-section-heading"><div><h2>Survivorship rules</h2><p>When sources describe the same entity, decide which value wins.</p></div><span className="mdm-chip neutral">Exact shared-key matching only</span></div>
              {entity.properties.map(p => <div className="mdm-rule-row" key={p.name}><span><strong>{p.label}</strong><code>{p.name}</code></span>
                <label>Conflict policy<select aria-label={`${p.label} conflict policy`} disabled={p.name === entity.key} value={p.strategy} onChange={e => changeProperty({ ...p, strategy: e.target.value as Property['strategy'], priority: e.target.value === 'require_equal' ? [] : contract.mappings.filter(m => m.target === p.name && sources.some(s => s.id === m.source)).map(m => m.source) })}>
                  <option value="require_equal">Require equal values</option><option value="source_priority">First non-null by source priority</option></select></label>
                {p.strategy === 'source_priority' ? <label>Priority, highest first<input aria-label={`${p.label} source priority`} value={p.priority.join(', ')} onChange={e => changeProperty({ ...p, priority: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} /></label>
                  : <p>Conflicting non-null values block the whole release.</p>}
              </div>)}
            </section>}
          </>}
          {page === 'structure' && <section className="mdm-card">
            <div className="mdm-section-heading"><div><h2>Source-to-semantic lineage</h2><p>Declared structure only. No guessed joins or inferred foreign keys.</p></div><button onClick={() => setEditor(JSON.stringify(contract, null, 2))}><Plus size={14} />Edit structure</button></div>
            <div className="mdm-lineage">{contract.entities.map(e => <div className="mdm-lineage-row" key={e.id}>
              <div className="mdm-source-stack">{contract.sources.filter(s => s.entity === e.id).map(s => <div key={s.id}><Database size={19} /><span><strong>{s.label}</strong><small>{s.schema}.{s.table}</small></span></div>)}</div>
              <ArrowRight className="mdm-lineage-arrow" /><div className="mdm-entity-card"><span className="mdm-eyebrow">ONTOLOGY ENTITY</span><h3><Boxes size={20} />{e.ontologyEntity}</h3>{e.properties.map(p => <div key={p.name}><span>{p.name === e.key ? 'KEY' : p.type}</span><strong>{p.name}</strong><small>{p.ontologyProperty}</small></div>)}</div>
              <ArrowRight className="mdm-lineage-arrow" /><div className="mdm-target-card"><span className="mdm-eyebrow">GOLD + DIRECT LAKE</span><h3>{e.label}</h3><code>gold.{e.goldTable}_&lt;release&gt;</code><p>One row per {e.key}<br />Attribute-level source provenance</p></div>
            </div>)}</div>
            <h3 className="mdm-relationships-heading">Relationships & dependencies</h3>{contract.relationships.map(r => <div className="mdm-relationship" key={r.name}><GitBranch size={19} /><strong>{r.from}.{r.column}</strong><span>many <ArrowRight size={16} /> one</span><strong>{r.to}.{contract.entities.find(e => e.id === r.to)?.key}</strong><small>{r.ontologyRelationship}</small></div>)}
            {!contract.relationships.length && <p className="mdm-muted">No declared relationships. No joins will be manufactured.</p>}
          </section>}
          {page === 'preview' && <section className="mdm-card">
            <div className="mdm-section-heading"><div><h2>Golden record simulation</h2><p>Sample-only evidence. The pipeline validates every source row before publishing.</p></div><div className="mdm-actions"><button onClick={() => chooseImport('samples')}><Upload size={14} />Load bounded sample</button><button onClick={() => setSamples({})}>Clear sample</button></div></div>
            {!simulation ? <div className="mdm-no-results">No sample loaded. Supply JSON keyed by source ID. Records remain in this tab's memory only.</div> : <>
              {simulation.conflicts.length > 0 && <div className="mdm-banner error"><CircleAlert size={20} /><span><strong>{simulation.conflicts.length} conflicts: release blocked</strong><br />{simulation.conflicts.join('\n')}</span></div>}
              {contract.entities.map(e => <div key={e.id} className="mdm-preview-table"><h3>{e.label}<span className="mdm-chip neutral">{simulation.rows[e.id]?.length ?? 0} eligible sample records</span></h3><table className="mdm-table"><thead><tr>{e.properties.map(p => <th key={p.name}>{p.label}</th>)}</tr></thead><tbody>{(simulation.rows[e.id] ?? []).map(row => <tr key={String(row[e.key])}>{e.properties.map(p => <td key={p.name}>{String(row[p.name] ?? '—')}<small>{simulation.lineage.find(l => l.entity === e.id && l.key === row[e.key] && l.property === p.name)?.source ?? 'No value'}</small></td>)}</tr>)}</tbody></table></div>)}
            </>}
          </section>}
          {page === 'releases' && <div className="mdm-release-grid"><section className="mdm-card">
            <div className="mdm-section-heading"><div><h2>Release readiness</h2><p>Publication is a pipeline responsibility, never a browser-side write.</p></div><ShieldCheck size={25} /></div>
            <label className="mdm-field">Product name<input value={contract.name} maxLength={255} onChange={e => setContract(c => ({ ...c, name: e.target.value }))} /></label>
            <label className="mdm-field">Ontology workspace GUID<input value={contract.ontology.workspaceId} onChange={e => { setContract(c => ({ ...c, ontology: { ...c.ontology, workspaceId: e.target.value, definitionHash: '' } })); setSamples({}) }} /></label>
            <label className="mdm-field">Ontology item GUID<input value={contract.ontology.itemId} onChange={e => { setContract(c => ({ ...c, ontology: { ...c.ontology, itemId: e.target.value, definitionHash: '' } })); setSamples({}) }} /></label>
            <button disabled={busy} onClick={() => void run(async () => { await verify(); setNotice('Live numeric generation 2, entity properties, keys, relationships and definition fingerprint verified. No ontology content was changed.') })}><Link2 size={15} />Verify live ontology</button>
            <div className="mdm-checklist">{issues.length ? issues.map((issue, index) => <p key={`${issue.code}-${index}`}><CircleAlert size={15} />{issue.message}</p>) : <p className="ready"><Check size={16} />Contract metadata has no unresolved blockers. Runtime validation is still required.</p>}</div>
            <div className="mdm-actions"><button onClick={() => download('master-data-draft.json', JSON.stringify(contract, null, 2))}><ArrowDownToLine size={15} />Download draft</button>
              <button className="primary" disabled={busy || example || issues.some(i => i.code !== 'ontology')} onClick={() => void run(release)}><ShieldCheck size={16} />Prepare release candidate</button></div>
            <p className="mdm-footnote">Pin the resulting SHA-256 in protected pipeline configuration after review. The runner rechecks ontology and schema drift, validates keys and conflicts, then writes an immutable Gold release and Direct Lake definition. A draft author is not automatically a production approver.</p>
          </section><section className="mdm-card"><div className="mdm-section-heading"><div><h2>SQL draft history</h2><p>Append-only, private to your signed-in identity.</p></div><button disabled={busy || !user} onClick={() => void run(() => historyPage())}>Refresh</button></div>
            {!history.length && <p className="mdm-muted">No drafts loaded. Connect to Fabric and save a draft. No browser storage fallback is used.</p>}
            {history.map(draft => <div className="mdm-history" key={draft.id}><span className="mdm-source-icon"><FileJson size={18} /></span><div><strong>{draft.name}</strong><small>{new Date(draft.createdAt).toLocaleString()}</small><code>{draft.contentHash.slice(0, 20)}</code></div><button disabled={busy} onClick={() => void run(async () => {
              const value = parseContract(JSON.parse(draft.contractJson))
              if (await fingerprint(value) !== draft.contentHash) throw new Error('Draft integrity check failed.')
              load(value); setNotice('Historical draft restored in memory. Saving creates a new revision.')
            })}>Restore</button></div>)}
            {hasMore && <button disabled={busy} onClick={() => void run(() => historyPage(true))}>Load older drafts</button>}
          </section></div>}
        </>}
        <footer className="mdm-footer"><span><Layers3 size={14} />Concord / Fabric master data stewardship</span><span>Explicit mappings. Traceable decisions. No silent merges.</span></footer>
      </main>
    </div>
    {editor !== null && <div className="mdm-modal-backdrop"><section className="mdm-modal" role="dialog" aria-modal="true" aria-labelledby="contract-editor-title"><div className="mdm-section-heading"><div><h2 id="contract-editor-title">Edit data product contract</h2><p>Define sources, canonical attributes, ontology mappings and declared relationships. Changes need review.</p></div><button aria-label="Close contract editor" onClick={() => { setEditor(null); setEditorError('') }}><X size={18} /></button></div>
      {editorError && <div className="mdm-banner error" role="alert">{editorError}</div>}
      <textarea aria-label="Contract JSON" spellCheck={false} value={editor} onChange={e => setEditor(e.target.value)} />
      <div className="mdm-actions"><button onClick={() => setEditor(null)}>Cancel</button><button className="primary" onClick={() => {
        try {
          const parsed = parseContract(JSON.parse(editor))
          // Editing the full definition invalidates all prior column attestations.
          load({ ...parsed, mappings: parsed.mappings.map(m => ({ ...m, reviewed: false })), ontology: { ...parsed.ontology, definitionHash: '' } })
          setEditor(null); setEditorError(''); setNotice('Draft replaced. Review column decisions and verify the live ontology before release.')
        } catch (error) { setEditorError(message(error)) }
      }}>Apply draft</button></div>
    </section></div>}
  </div>
}

function MappingPanel({ entity, source, column, contract, existing, onSave, onAdd }: {
  entity: Entity; source: Source; column: string; contract: Contract; existing?: Mapping; onSave: (mapping: Mapping) => void; onAdd: (property: Property) => void
}) {
  const isKey = column === source.keyColumn
  const [target, setTarget] = useState(isKey ? entity.key : existing?.target ?? '')
  const [transform, setTransform] = useState<Transform>(existing?.transform ?? 'identity')
  const [reason, setReason] = useState(existing?.reason ?? '')
  const [exclude, setExclude] = useState(!isKey && existing ? existing.target === null : false)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState(column)
  const [newLabel, setNewLabel] = useState(column.replaceAll('_', ' '))
  const [ontologyProperty, setOntologyProperty] = useState(column)
  const matches = suggestions(contract, source, column)
  const peers = columnPeers(contract, source, column)
  return <aside className="mdm-detail">
    <div className="mdm-eyebrow">COLUMN DECISION</div><h3>{column}</h3><p className="mdm-muted">{source.label} / {source.schema}.{source.table}</p>
    {matches.length > 0 && !existing && <div className="mdm-suggestion"><Sparkles size={15} /><span>Name-based suggestion: <strong>{matches.join(', ')}</strong><small>Confirm the business meaning before mapping.</small></span></div>}
    {peers.length > 0 && <div className="mdm-suggestion"><Link2 size={15} /><span>Compare with <strong>{peers.map(p => `${p.source}.${p.column}`).join(', ')}</strong><small>Shared names or targets are review evidence, not proof of matching records.</small></span></div>}
    <label className="mdm-field">Canonical attribute<select value={exclude ? '' : target} disabled={exclude || isKey} onChange={e => setTarget(e.target.value)}>
      <option value="">Select an attribute</option>{entity.properties.map(p => <option key={p.name} value={p.name}>{p.name} ({p.type})</option>)}</select></label>
    {!isKey && !adding && <button className="mdm-add-attribute" onClick={() => setAdding(true)}><Plus size={13} />Define a new canonical attribute</button>}
    {adding && <div className="mdm-new-attribute">
      <label className="mdm-field">Canonical name<input value={newName} onChange={e => setNewName(e.target.value)} placeholder="lower_snake_case" /></label>
      <label className="mdm-field">Semantic label<input value={newLabel} onChange={e => setNewLabel(e.target.value)} /></label>
      <label className="mdm-field">Declared ontology property<input value={ontologyProperty} onChange={e => setOntologyProperty(e.target.value)} /></label>
      <button disabled={!/^[a-z][a-z0-9_]{0,62}$/.test(newName) || !newLabel.trim() || !ontologyProperty.trim()
        || entity.properties.some(p => p.name === newName || p.ontologyProperty === ontologyProperty)} onClick={() => {
        onAdd({ name: newName, ontologyProperty, label: newLabel, type: source.columns.find(c => c.name === column)!.type,
          required: false, strategy: 'require_equal', priority: [] })
        setTarget(newName); setAdding(false)
      }}>Add to draft</button><button onClick={() => setAdding(false)}>Cancel</button>
    </div>}
    <label className="mdm-field">Normalization<select value={transform} disabled={exclude || isKey} onChange={e => setTransform(e.target.value as Transform)}><option value="identity">Keep original value</option><option value="trim">Trim whitespace</option><option value="lower">Trim + lowercase</option><option value="upper">Trim + uppercase</option></select></label>
    <label className="mdm-checkbox"><input type="checkbox" checked={exclude} disabled={isKey} onChange={e => setExclude(e.target.checked)} />Exclude from Gold</label>
    <label className="mdm-field">Business rationale<textarea rows={4} maxLength={2048} placeholder="Why does this mapping or exclusion make business sense?" value={reason} onChange={e => setReason(e.target.value)} /></label>
    <div className="mdm-detail-note"><ShieldCheck size={17} /><p>Mapping changes reset review. Rule changes require all affected mappings to be reviewed again.</p></div>
    <button className="primary" disabled={!reason.trim() || (!exclude && !target)} onClick={() => onSave({
      source: source.id, column, target: exclude ? null : target, transform: exclude || isKey ? 'identity' : transform, reason, reviewed: true,
    })}><Check size={16} />Review & apply decision</button>
  </aside>
}
