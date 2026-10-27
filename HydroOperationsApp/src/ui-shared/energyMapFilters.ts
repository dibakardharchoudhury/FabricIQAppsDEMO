import type { EnergyFeature } from './energyMapModel'

export type NumericRange = { min: number; max: number }
export type HydroPropertyFilters = {
  owners: string[]
  capacity: NumericRange | null
  inOperation: 'all' | 'true' | 'false'
  priceAreas: string[]
  grossHead: NumericRange | null
  plantStatus: string | null
}
export type TransformerPropertyFilters = {
  owners: string[]
  sourceLayers: string[]
  voltage: NumericRange | null
  networkLevels: string[]
}
export type EnergyPropertyFilters = {
  hydro: HydroPropertyFilters
  transformers: TransformerPropertyFilters
}
export type EnergyPropertyOptions = {
  hydro: { owners: string[]; priceAreas: string[]; plantStatuses: string[]; capacityMax: number | null; grossHeadMax: number | null }
  transformers: { owners: string[]; sourceLayers: string[]; networkLevels: string[]; voltageMax: number | null }
}

const FACET_LIMIT = 5000

export function createEnergyPropertyFilters(): EnergyPropertyFilters {
  return {
    hydro: { owners: [], capacity: null, inOperation: 'all', priceAreas: [], grossHead: null, plantStatus: null },
    transformers: { owners: [], sourceLayers: [], voltage: null, networkLevels: [] },
  }
}

export function propertyFilterCount(filters: EnergyPropertyFilters): number {
  const { hydro, transformers } = filters
  return [
    hydro.owners.length > 0, hydro.capacity !== null, hydro.inOperation !== 'all',
    hydro.priceAreas.length > 0, hydro.grossHead !== null, hydro.plantStatus !== null,
    transformers.owners.length > 0, transformers.sourceLayers.length > 0,
    transformers.voltage !== null, transformers.networkLevels.length > 0,
  ].filter(Boolean).length
}

function validateString(value: string): void {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('Invalid map property selection.')
}

function validateSelection(values: string[]): void {
  if (!Array.isArray(values) || values.length > FACET_LIMIT) throw new Error('Invalid map property selection.')
  values.forEach(validateString)
}

function validateRange(range: NumericRange | null): void {
  if (range === null) return
  if (!range || !Number.isFinite(range.min) || !Number.isFinite(range.max) || range.min < 0 || range.max < range.min) {
    throw new Error('Invalid map property range.')
  }
}

export function validateEnergyPropertyFilters(filters: EnergyPropertyFilters): void {
  const { hydro, transformers } = filters
  if (!['all', 'true', 'false'].includes(hydro.inOperation)) throw new Error('Invalid in-operation filter.')
  ;[hydro.owners, hydro.priceAreas, transformers.owners, transformers.sourceLayers, transformers.networkLevels].forEach(validateSelection)
  ;[hydro.capacity, hydro.grossHead, transformers.voltage].forEach(validateRange)
  if (hydro.plantStatus !== null) validateString(hydro.plantStatus)
}

function matchesSelection(value: unknown, selected: string[]): boolean {
  return !selected.length || selected.includes(value === null || value === undefined ? '' : String(value))
}

function matchesRange(value: unknown, range: NumericRange | null): boolean {
  if (!range) return true
  if ((typeof value !== 'number' && typeof value !== 'string') || (typeof value === 'string' && !value.trim())) return false
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric >= range.min && numeric <= range.max
}

function booleanProperty(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number' && Number.isFinite(value)) return value !== 0
  if (typeof value === 'string') {
    const text = value.trim().toLowerCase()
    if (text === 'true' || text === '1') return true
    if (text === 'false' || text === '0') return false
  }
  return null
}

export function matchesEnergyPropertyFilters(feature: Pick<EnergyFeature, 'layerId' | 'properties'>, filters: EnergyPropertyFilters): boolean {
  const p = feature.properties
  if (feature.layerId === 'hydro-plants') {
    const f = filters.hydro
    return matchesSelection(p.owner, f.owners) && matchesRange(p.installed_capacity_mw, f.capacity)
      && (f.inOperation === 'all' || booleanProperty(p.in_operation) === (f.inOperation === 'true'))
      && matchesSelection(p.price_area, f.priceAreas) && matchesRange(p.gross_head_m, f.grossHead)
      && (f.plantStatus === null || matchesSelection(p.plant_status, [f.plantStatus]))
  }
  if (feature.layerId === 'transformers') {
    const f = filters.transformers
    return matchesSelection(p.owner, f.owners) && matchesSelection(p.source_layer, f.sourceLayers)
      && matchesRange(p.voltage_kv, f.voltage) && matchesSelection(p.network_level, f.networkLevels)
  }
  return true
}

export function parseEnergyPropertyOptions(rows: Record<string, unknown>[]): EnergyPropertyOptions {
  const hydro = rows.find(row => row.layer_id === 'hydro-plants')
  const transformers = rows.find(row => row.layer_id === 'transformers')
  if (rows.length !== 2 || !hydro || !transformers) throw new Error('Property filter metadata is incomplete. Check the energy import.')
  const options = (value: unknown): string[] => {
    const parsed: unknown = typeof value === 'string' ? JSON.parse(value) : value
    if (!Array.isArray(parsed) || parsed.length > FACET_LIMIT
      || parsed.some(item => typeof item !== 'string' || item.length > 2048)) {
      throw new Error('Property filter options are invalid or exceed the supported limit.')
    }
    return [...new Set<string>(parsed)].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
  }
  const maximum = (value: unknown): number | null => {
    if (value === null) return null
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('Invalid numeric limits in map property metadata.')
    return value
  }
  return {
    hydro: {
      owners: options(hydro.owners), priceAreas: options(hydro.price_areas), plantStatuses: options(hydro.plant_statuses),
      capacityMax: maximum(hydro.capacity_max), grossHeadMax: maximum(hydro.gross_head_max),
    },
    transformers: {
      owners: options(transformers.owners), sourceLayers: options(transformers.source_layers),
      networkLevels: options(transformers.network_levels), voltageMax: maximum(transformers.voltage_max),
    },
  }
}
