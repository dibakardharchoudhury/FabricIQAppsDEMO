import { useEffect, useId, useRef, useState } from 'react'
import {
  createEnergyPropertyFilters, type EnergyPropertyFilters, type EnergyPropertyOptions,
  type HydroPropertyFilters, type NumericRange, type TransformerPropertyFilters,
} from '../../energyMapFilters'
import type { EnergyLayerId } from '../../energyMapModel'

export function EnergyPropertyFiltersPanel({ value, options, error, layers, zoom, onChange, onLayerChange }: {
  value: EnergyPropertyFilters
  options?: EnergyPropertyOptions
  error?: string
  layers: EnergyLayerId[]
  zoom: number
  onChange: (value: EnergyPropertyFilters) => void
  onLayerChange: (layer: EnergyLayerId, enabled: boolean) => void
}) {
  const operationLabelId = useId()
  const hydro = (patch: Partial<HydroPropertyFilters>) => onChange({ ...value, hydro: { ...value.hydro, ...patch } })
  const transformers = (patch: Partial<TransformerPropertyFilters>) => onChange({ ...value, transformers: { ...value.transformers, ...patch } })
  return <section className="energy-map-property-content" aria-label="Map property filters">
    {!options && !error && <p role="status">Loading property values from Fabric...</p>}
    {error && <p className="energy-map-stale" role="alert">{error} {options && 'Previously loaded options are still available.'}</p>}
    <p className="energy-map-property-help">Filters apply only to their own asset type. Other selected layers stay visible. Options cover all imported records; the map shows located features in the current viewport.</p>
    <fieldset>
      <legend>Hydropower plants</legend>
      <div className="energy-map-property-heading">
        <label><input type="checkbox" checked={layers.includes('hydro-plants')} onChange={event => onLayerChange('hydro-plants', event.target.checked)} />Show hydropower plants</label>
        <button type="button" onClick={() => onChange({ ...value, hydro: createEnergyPropertyFilters().hydro })}>Reset plant filters</button>
      </div>
      <div className="energy-map-property-grid">
        <MultiSelect label="Owner" options={options?.hydro.owners} selected={value.hydro.owners} onChange={owners => hydro({ owners })} />
        <RangeFilter label="Capacity" unit="MW" maximum={options?.hydro.capacityMax} value={value.hydro.capacity} onChange={capacity => hydro({ capacity })} />
        <label className="energy-map-property-field"><span id={operationLabelId}>In operation</span>
          <select aria-labelledby={operationLabelId} disabled={!options} value={value.hydro.inOperation} onChange={event => {
            const choice = event.target.value
            if (choice !== 'all' && choice !== 'true' && choice !== 'false') throw new Error('Invalid in-operation choice.')
            hydro({ inOperation: choice })
          }}><option value="all">All</option><option value="true">True</option><option value="false">False</option></select>
        </label>
        <MultiSelect label="Price area" options={options?.hydro.priceAreas} selected={value.hydro.priceAreas} onChange={priceAreas => hydro({ priceAreas })} />
        <RangeFilter label="Gross head" unit="m" maximum={options?.hydro.grossHeadMax} value={value.hydro.grossHead} onChange={grossHead => hydro({ grossHead })} />
        <SingleSelect label="Plant status" options={options?.hydro.plantStatuses} value={value.hydro.plantStatus} onChange={plantStatus => hydro({ plantStatus })} />
      </div>
      <p className="energy-map-property-help">Owner is NVE's main owner. Capacity is installed capacity, not current generation.</p>
    </fieldset>
    <fieldset>
      <legend>Transformer substations</legend>
      <div className="energy-map-property-heading">
        <label><input type="checkbox" checked={layers.includes('transformers')} onChange={event => onLayerChange('transformers', event.target.checked)} />Show transformer substations</label>
        <button type="button" onClick={() => onChange({ ...value, transformers: createEnergyPropertyFilters().transformers })}>Reset transformer filters</button>
      </div>
      {zoom < 8 && <p className="energy-map-property-help">Zoom to level 8 or closer to display transformer substations.</p>}
      <div className="energy-map-property-grid">
        <MultiSelect label="Owner" options={options?.transformers.owners} selected={value.transformers.owners} onChange={owners => transformers({ owners })} />
        <MultiSelect label="Source layer" options={options?.transformers.sourceLayers} selected={value.transformers.sourceLayers} onChange={sourceLayers => transformers({ sourceLayers })} />
        <RangeFilter label="Voltage" unit="kV" maximum={options?.transformers.voltageMax} value={value.transformers.voltage} onChange={voltage => transformers({ voltage })} />
        <MultiSelect label="Network level" options={options?.transformers.networkLevels} selected={value.transformers.networkLevels} onChange={networkLevels => transformers({ networkLevels })} />
      </div>
      <p className="energy-map-property-help">Source layer and network level use NVE's source codes. The current transformer dataset is source layer 5.</p>
    </fieldset>
    <p className="energy-map-property-help">Missing values are included by default. Narrowing a numeric range excludes records without that measurement; select "Not provided" in a dropdown to find missing categorical values.</p>
  </section>
}

function optionLabel(value: string): string {
  return value || 'Not provided'
}

function MultiSelect({ label, options, selected, onChange }: {
  label: string; options?: string[]; selected: string[]; onChange: (values: string[]) => void
}) {
  const id = useId()
  const details = useRef<HTMLDetailsElement>(null)
  const summary = useRef<HTMLElement>(null)
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && details.current && !details.current.contains(event.target)) details.current.open = false
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [])
  const choices = [...new Set([...(options ?? []), ...selected])]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .filter(option => optionLabel(option).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
  return <div className="energy-map-property-field">
    <span id={id}>{label}</span>
    <details className="energy-map-multiselect" ref={details} onToggle={event => {
      setOpen(event.currentTarget.open)
      if (!event.currentTarget.open) setSearch('')
    }} onKeyDown={event => {
      if (event.key === 'Escape' && details.current) {
        details.current.open = false
        summary.current?.focus()
        event.stopPropagation()
      }
    }} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget) && details.current) details.current.open = false
    }}>
      <summary ref={summary} aria-labelledby={`${id} ${id}-selection`} aria-disabled={!options} tabIndex={options ? 0 : -1}
        onClick={event => { if (!options) event.preventDefault() }}>
        <span id={`${id}-selection`}>{selected.length === 1 ? optionLabel(selected[0]) : selected.length ? `${selected.length} selected` : 'All'}</span>
      </summary>
      {open && <div className="energy-map-multiselect-menu" role="group" aria-label={`${label} options`}>
        <input type="search" aria-label={`Search ${label.toLowerCase()}`} placeholder={`Search ${label.toLowerCase()}`} value={search} onChange={event => setSearch(event.target.value)} />
        <button type="button" onClick={() => onChange([])}>Clear selection (all)</button>
        <div className="energy-map-multiselect-options">
          {choices.map(option => <label key={option}>
            <input type="checkbox" checked={selected.includes(option)} onChange={event =>
              onChange(event.target.checked ? [...selected, option] : selected.filter(value => value !== option))} />
            <span>{optionLabel(option)}</span>
          </label>)}
          {!choices.length && <p>No matching values.</p>}
        </div>
      </div>}
    </details>
  </div>
}

function SingleSelect({ label, options, value, onChange }: {
  label: string; options?: string[]; value: string | null; onChange: (value: string | null) => void
}) {
  const id = useId()
  const choices = [...new Set([...(options ?? []), ...(value === null ? [] : [value])])]
  return <label className="energy-map-property-field"><span id={id}>{label}</span>
    <select aria-labelledby={id} disabled={!options} value={value === null ? 'all' : `value:${value}`} onChange={event => {
      const choice = event.target.value
      if (choice === 'all') onChange(null)
      else if (choice.startsWith('value:')) onChange(choice.slice(6))
      else throw new Error('Invalid map property choice.')
    }}>
      <option value="all">All</option>
      {choices.map(option => <option key={option} value={`value:${option}`}>{optionLabel(option)}</option>)}
    </select>
  </label>
}

function RangeFilter({ label, unit, maximum, value, onChange }: {
  label: string; unit: string; maximum?: number | null; value: NumericRange | null; onChange: (value: NumericRange | null) => void
}) {
  const id = useId()
  const available = maximum !== null && maximum !== undefined
  const max = Math.max(maximum ?? 0, value?.max ?? 0)
  const range = value ?? { min: 0, max }
  const format = (number: number) => number.toLocaleString(undefined, { maximumFractionDigits: 3 })
  const update = (end: 'min' | 'max', raw: string) => {
    const number = Number(raw)
    if (!Number.isFinite(number)) throw new Error('Invalid map range value.')
    const rounded = number === 0 || number === max ? number : Math.min(max, Math.max(0, Math.round(number * 1000) / 1000))
    const next = end === 'min' ? { min: Math.min(rounded, range.max), max: range.max }
      : { min: range.min, max: Math.max(rounded, range.min) }
    onChange(next.min === 0 && next.max === max ? null : next)
  }
  return <div className="energy-map-property-field energy-map-range" role="group" aria-labelledby={id}>
    <span id={id}>{label} ({unit})</span>
    {available || value ? <output>{format(range.min)} - {format(range.max)} {unit}</output>
      : <small>{maximum === undefined ? 'Waiting for data' : 'No numeric values provided'}</small>}
    <label><span>Min</span><input aria-label={`${label} minimum`} type="range" min={0} max={max} step="any"
      aria-valuetext={`${format(range.min)} ${unit}`}
      disabled={!available || max === 0} value={range.min} onChange={event => update('min', event.target.value)} /></label>
    <label><span>Max</span><input aria-label={`${label} maximum`} type="range" min={0} max={max} step="any"
      aria-valuetext={`${format(range.max)} ${unit}`}
      disabled={!available || max === 0} value={range.max} onChange={event => update('max', event.target.value)} /></label>
    {value && <button type="button" onClick={() => onChange(null)}>Use full range</button>}
    {available && max !== maximum && <small>Current data maximum: {format(maximum)} {unit}; your selection is retained.</small>}
  </div>
}
