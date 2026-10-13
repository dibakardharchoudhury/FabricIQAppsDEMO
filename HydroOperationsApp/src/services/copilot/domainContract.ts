import { ASSET_ENTITIES, KUSTO_SOURCES, OPERATIONS_ENTITIES } from './catalog.ts'

export const DOMAIN_CONTRACT_VERSION = 3

export const ANSWER_PRESENTATIONS = [
  'direct_records_table',
  'direct_records_bar',
  'direct_records_line',
  'direct_records_scatter',
  'direct_grouped_by_equipment_table',
  'direct_grouped_by_equipment_bar',
  'direct_grouped_by_equipment_line',
  'direct_grouped_by_equipment_scatter',
  'direct_grouped_by_facility_table',
  'direct_grouped_by_facility_bar',
  'direct_grouped_by_facility_line',
  'direct_grouped_by_facility_scatter',
  'native_records_table',
  'native_records_bar',
  'native_records_line',
  'native_records_scatter',
  'native_grouped_by_equipment_table',
  'native_grouped_by_equipment_bar',
  'native_grouped_by_equipment_line',
  'native_grouped_by_equipment_scatter',
  'native_grouped_by_facility_table',
  'native_grouped_by_facility_bar',
  'native_grouped_by_facility_line',
  'native_grouped_by_facility_scatter',
  'reconciliation_table',
] as const

type DomainDatasetDefinition = {
  key: string
  source: string
  entity: string
  identityFields: readonly string[]
  presentations: readonly string[]
  evidenceScopes: readonly ('population' | 'focus')[]
  evidenceAuthorities: readonly ('direct' | 'native')[]
  limitations?: readonly string[]
}

export const DOMAIN_DATASETS = [
  { key: 'facilities', source: 'lakehouse', entity: 'facilities', identityFields: ['facility_id'], presentations: ['records_table'], evidenceScopes: ['population'], evidenceAuthorities: ['direct'] },
  { key: 'equipment', source: 'lakehouse', entity: 'equipment', identityFields: ['equipment_id'], presentations: ['records_table'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['direct'] },
  { key: 'instruments', source: 'lakehouse', entity: 'instruments', identityFields: ['instrument_id'], presentations: ['records_table'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['direct'] },
  { key: 'telemetry', source: 'eventhouse', entity: 'OPCUAEvents', identityFields: ['opcua_node_id', 'event_time'], presentations: ['records_table', 'records_line', 'records_scatter'], evidenceScopes: ['focus'], evidenceAuthorities: ['direct'] },
  { key: 'signal_quality', source: 'eventhouse', entity: 'OPCUAEvents', identityFields: ['opcua_node_id', 'event_time'], presentations: ['records_table'], evidenceScopes: ['population'], evidenceAuthorities: ['direct'] },
  { key: 'temperature', source: 'eventhouse', entity: 'OPCUAEvents', identityFields: ['opcua_node_id', 'event_time'], presentations: ['records_table', 'records_bar'], evidenceScopes: ['population'], evidenceAuthorities: ['direct'] },
  {
    key: 'station_power', source: 'eventhouse', entity: 'OPCUAEvents', identityFields: ['Station'],
    presentations: ['records_table', 'records_bar', 'records_line', 'grouped_by_facility_table', 'grouped_by_facility_bar'],
    evidenceScopes: ['population'], evidenceAuthorities: ['direct'],
    limitations: [
      'Station power is a sample-weighted arithmetic mean of converted power readings with all qualities included; '
        + 'it is not total station output, a time-weighted mean, or energy.',
    ],
  },
  { key: 'work_orders', source: 'sql', entity: 'work_orders', identityFields: ['workOrderNumber'], presentations: ['records_table'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['direct'] },
  { key: 'work_backlog', source: 'derived', entity: 'work_orders', identityFields: ['group_id'], presentations: ['grouped_by_equipment_table', 'grouped_by_equipment_bar', 'grouped_by_facility_table', 'grouped_by_facility_bar'], evidenceScopes: ['population'], evidenceAuthorities: ['direct'] },
  { key: 'inspections', source: 'sql', entity: 'inspections', identityFields: ['id'], presentations: ['records_table'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['direct'] },
  { key: 'notifications', source: 'sql', entity: 'notifications', identityFields: ['id'], presentations: ['records_table'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['direct'] },
  { key: 'spare_parts', source: 'sql', entity: 'spare_parts', identityFields: ['id'], presentations: ['records_table'], evidenceScopes: ['population'], evidenceAuthorities: ['direct'] },
  { key: 'asset_models', source: 'sql', entity: 'asset_models', identityFields: ['id'], presentations: ['records_table'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['direct'] },
  { key: 'native_claims', source: 'native', entity: 'native_claims', identityFields: ['source_identity'], presentations: ['records_table', 'grouped_by_equipment_table', 'grouped_by_equipment_bar', 'grouped_by_facility_table', 'grouped_by_facility_bar'], evidenceScopes: ['population', 'focus'], evidenceAuthorities: ['native'] },
] as const satisfies readonly DomainDatasetDefinition[]

export const DOMAIN_DATASET_KEYS = DOMAIN_DATASETS.map(dataset => dataset.key)
export const DOMAIN_EVIDENCE_REQUIREMENTS = DOMAIN_DATASETS.flatMap(dataset =>
  dataset.evidenceScopes.flatMap(scope =>
    dataset.evidenceAuthorities.map(authority => `${dataset.key}_${scope}_${authority}`),
  ),
)
export const STATION_POWER_SEMANTICS = (DOMAIN_DATASETS as readonly DomainDatasetDefinition[])
  .find(dataset => dataset.key === 'station_power')!.limitations![0]

type EvidenceDatasetDescription = {
  kind: string
  field: string
  authority: 'direct'
  scope: 'population' | 'focus'
  complete: boolean
  identity_fields: string[]
  group_by: 'equipment_id' | 'facility_id' | null
  role: 'primary' | 'support' | 'audit'
  limitations: string[]
}

function datasetIdentity(kind: string): string[] {
  const dataset = DOMAIN_DATASETS.find(candidate => candidate.key === kind)
  if (!dataset) throw new Error(`Unknown governed dataset ${kind}.`)
  return [...dataset.identityFields]
}

function hasEquipmentFilter(args: Record<string, unknown>): boolean {
  if (typeof args.equipment_id === 'string' || Array.isArray(args.equipment_ids)) return true
  const where = args.where
  return Array.isArray(where) && where.some(condition =>
    condition && typeof condition === 'object' && !Array.isArray(condition)
      && ['equipment_id', 'equipmentId'].includes(String((condition as Record<string, unknown>).column)),
  )
}

export function describeToolEvidence(
  tool: string, args: Record<string, unknown>, result: Record<string, unknown>,
): EvidenceDatasetDescription[] {
  const complete = result.truncated === false
    || ['query_work_backlog', 'query_station_power', 'query_signal_quality_snapshot',
      'query_turbine_temperature_snapshot', 'propose_work_order'].includes(tool)
  const scope = hasEquipmentFilter(args) || tool === 'query_telemetry' ? 'focus' : 'population'
  const describe = (
    kind: string, field = 'rows', options: Partial<EvidenceDatasetDescription> = {},
  ): EvidenceDatasetDescription => ({
    kind, field, authority: 'direct', scope, complete,
    identity_fields: datasetIdentity(kind), group_by: null, role: 'primary',
    limitations: [...((DOMAIN_DATASETS as readonly DomainDatasetDefinition[])
      .find(candidate => candidate.key === kind)?.limitations ?? [])],
    ...options,
  })
  if (tool === 'query_assets') {
    if (!['facilities', 'equipment', 'instruments'].includes(String(args.entity))) {
      throw new Error('Asset tool selected an ungoverned dataset.')
    }
    return [describe(String(args.entity), 'rows', { role: 'support' })]
  }
  if (tool === 'query_operations') {
    if (!['work_orders', 'inspections', 'notifications', 'spare_parts', 'asset_models']
      .includes(String(args.entity))) {
      throw new Error('Operations tool selected an ungoverned dataset.')
    }
    return [describe(String(args.entity))]
  }
  if (tool === 'query_work_backlog') {
    const groupBy = args.group_by === 'facility' ? 'facility_id' : 'equipment_id'
    return [
      describe('work_backlog', 'rows', {
        scope: 'population', complete: true, group_by: groupBy,
        identity_fields: [groupBy],
      }),
      describe('work_orders', 'open_work_orders', {
        scope: 'population', complete: true,
      }),
    ]
  }
  if (tool === 'query_telemetry' || tool === 'run_kql') return [describe('telemetry')]
  if (tool === 'query_station_power') {
    return [describe('station_power', 'rows', {
      scope: 'population', complete: true, group_by: 'facility_id',
    })]
  }
  if (tool === 'query_signal_quality_snapshot') {
    return [describe('signal_quality', 'rows', { scope: 'population', complete: true })]
  }
  if (tool === 'query_turbine_temperature_snapshot') {
    return [describe('temperature', 'rows', { scope: 'population', complete: true })]
  }
  if (tool === 'propose_work_order') {
    return [describe('work_orders', 'existing_work', {
      scope: 'focus', complete: true, role: 'audit',
    })]
  }
  throw new Error(`Tool ${tool} has no governed evidence dataset mapping.`)
}

export type DomainEndpoint = {
  source: 'lakehouse' | 'sql' | 'eventhouse'
  entity: string
  column: string
}

export type DomainRelationship = {
  key: string
  from: DomainEndpoint
  to: DomainEndpoint
  cardinality: 'many-to-one' | 'one-to-one' | 'many-to-many'
  meaning: 'identity' | 'signal-identity' | 'type-context'
  description: string
  limitations?: string[]
}

export const DOMAIN_RELATIONSHIPS: DomainRelationship[] = [
  {
    key: 'equipment-facility',
    from: { source: 'lakehouse', entity: 'equipment', column: 'facility_id' },
    to: { source: 'lakehouse', entity: 'facilities', column: 'facility_id' },
    cardinality: 'many-to-one',
    meaning: 'identity',
    description: 'Authoritative equipment-to-facility membership.',
  },
  {
    key: 'instrument-equipment',
    from: { source: 'lakehouse', entity: 'instruments', column: 'equipment_id' },
    to: { source: 'lakehouse', entity: 'equipment', column: 'equipment_id' },
    cardinality: 'many-to-one',
    meaning: 'identity',
    description: 'Authoritative instrument-to-equipment membership.',
  },
  {
    key: 'instrument-facility',
    from: { source: 'lakehouse', entity: 'instruments', column: 'facility_id' },
    to: { source: 'lakehouse', entity: 'facilities', column: 'facility_id' },
    cardinality: 'many-to-one',
    meaning: 'identity',
    description: 'Denormalized authoritative instrument-to-facility membership.',
  },
  {
    key: 'telemetry-instrument',
    from: { source: 'eventhouse', entity: 'OPCUAEvents', column: 'opcua_node_id' },
    to: { source: 'lakehouse', entity: 'instruments', column: 'opcua_node_id' },
    cardinality: 'many-to-one',
    meaning: 'signal-identity',
    description: 'Authoritative telemetry reading-to-instrument identity.',
  },
  ...[
    ['work_orders', 'equipmentId'],
    ['inspections', 'equipmentId'],
    ['notifications', 'equipmentId'],
    ['asset_models', 'equipmentId'],
  ].map(([entity, column]): DomainRelationship => ({
    key: `${entity}-equipment`,
    from: { source: 'sql', entity, column },
    to: { source: 'lakehouse', entity: 'equipment', column: 'equipment_id' },
    cardinality: 'many-to-one',
    meaning: 'identity',
    description: `Authoritative ${entity}-to-equipment identity.`,
  })),
  ...[
    ['work_orders', 'opcuaNodeId'],
    ['inspections', 'opcuaNodeId'],
    ['notifications', 'opcuaNodeId'],
  ].map(([entity, column]): DomainRelationship => ({
    key: `${entity}-signal`,
    from: { source: 'sql', entity, column },
    to: { source: 'lakehouse', entity: 'instruments', column: 'opcua_node_id' },
    cardinality: 'many-to-one',
    meaning: 'signal-identity',
    description: `Exact ${entity}-to-signal identity when the source field is populated.`,
    limitations: ['A missing signal identity does not remove the record from equipment-level coverage.'],
  })),
  {
    key: 'work-order-instrument',
    from: { source: 'sql', entity: 'work_orders', column: 'instrumentId' },
    to: { source: 'lakehouse', entity: 'instruments', column: 'instrument_id' },
    cardinality: 'many-to-one',
    meaning: 'signal-identity',
    description: 'Exact work-order-to-instrument identity when instrumentId is populated.',
    limitations: ['A missing instrument identity does not remove the order from equipment-level coverage.'],
  },
  {
    key: 'spare-part-equipment-type',
    from: { source: 'sql', entity: 'spare_parts', column: 'equipmentType' },
    to: { source: 'lakehouse', entity: 'equipment', column: 'equipment_type_code' },
    cardinality: 'many-to-many',
    meaning: 'type-context',
    description: 'Equipment-type context for inventory planning.',
    limitations: [
      'Type equality is not an equipment-specific BOM, compatibility, reservation, procurement, or work-order relation.',
    ],
  },
]

export const DOMAIN_SEMANTICS = {
  open_work: {
    entity: 'work_orders',
    status_column: 'status',
    excluded_statuses_case_insensitive: ['completed', 'cancelled'],
    includes: ['Draft', 'Planned'],
  },
  telemetry_freshness: {
    event_time_column: 'event_time',
    compare_with: 'read_completed_at_utc',
    stale_after_seconds: 60,
    future_reading: 'uncertain',
  },
  signal_quality: {
    bad_literal: 'BAD',
    interpretation: 'telemetry signal quality, not a physical diagnosis',
  },
  work_coverage: {
    same_signal_requires: ['instrumentId=instrument_id', 'opcuaNodeId=opcua_node_id'],
    equipment_level_requires: 'equipmentId=equipment_id',
    completeness: 'Every open order on affected equipment must be accounted for exactly once.',
  },
  derived_measures: {
    record_count: 'Application-derived count of the complete, filtered source rows in the declared grouping scope.',
  },
  empty_results: {
    certified_zero: 'A successful complete scoped read with row_count 0 means zero matching records in that scope.',
    unavailable_is_not_zero: 'A failed, unavailable or truncated source must never be presented as zero matching records.',
    presentation: 'Show the zero count, exact scope and read_completed_at_utc instead of omitting the source result.',
  },
} as const

export const OPERATIONAL_EVIDENCE_CONTRACT = `Operational evidence contract:
"Operational SQL work orders" means records from the operational SQL data source, not a work-order type or category. Do not invent a SQL/type/category filter. Open means status neither Completed nor Cancelled.
For parts at or below reorder level, filter quantityOnHand with op lte and value_column reorderLevel. A column name in value is not a literal threshold. Report actual matching parts, including equality; stock/type alone does not prove an equipment-specific BOM or work-order relation.
For each affected equipment ID, include EVERY open work order and its number, title, status and priority. Same-signal work requires an exact instrument ID or OPC UA node match to the signal being discussed. EVERY other open order on that equipment is equipment-level work, including orders linked to a different signal; equipment-level does not mean only orders with null signal IDs. These two groups must account for all open orders on that equipment.
Draft and Planned orders already represent open planned work. Lack of an executed inspection or a completed result does not make their covered purpose uncovered. Do not justify a second conditional draft merely because the existing order is Draft, unscheduled or unexecuted. A distinct uncovered purpose requires specific evidence beyond status; otherwise recommend reviewing the existing order without claiming to attach, merge, cancel or promote it (those mutation tools are unavailable).
When a compound request selects one turbine for investigation, preserve the requested work inventory for ALL initially affected turbines in the final answer, not only the selected turbine.
Compare telemetry event timestamps with the tool's read_completed_at_utc when provided; it is the actual read-completion clock, not the request-start clock. For remote telemetry queries, request the query's UTC clock with the evidence when supported. Otherwise identify the supplied current UTC time as the request-start reference, not an invented query time. A reading received after request start does not establish source clock skew. Never use the newest event as the current clock. Explicitly label readings older than 60 seconds as stale; readings ahead of the actual read-completion clock have uncertain freshness. Never describe a reading as fresh merely because it falls inside the lookback window.
The telemetry 60-second rule does not apply to business dates. Inventory lastRestockedAt, work-order createdAt and notification dates describe business events, not when the current SQL record was read or last synchronized. Do not declare inventory stale from an old restock date alone; state that synchronization freshness is unknown when no authoritative sync timestamp exists. Missing BOM, procurement, dispatch or gateway-log sources are evidence requirements, not callable tools: request operator-provided evidence or a new integration rather than offering to query unavailable tables.
When forwarding a request to another agent or remote tool, preserve these source and matching rules, the current time, and the original scope.`

type DomainRow = Record<string, unknown>

export type FacilityWorkBacklog = {
  group_by: 'facility'
  rows: Array<{
    facility_id: string
    facility_name: string | null
    equipment_with_open_work: number
    open_work_order_count: number
  }>
  open_work_orders: DomainRow[]
  unmatched: Array<{ workOrderNumber: string; equipmentId: string; reason: string }>
  semantics: string
}

function requiredString(row: DomainRow, column: string): string {
  const value = row[column]
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Domain source omitted ${column}.`)
  return value
}

function uniqueIndex(rows: readonly DomainRow[], column: string): Map<string, DomainRow> {
  const result = new Map<string, DomainRow>()
  for (const row of rows) {
    const value = requiredString(row, column)
    if (result.has(value)) throw new Error(`Domain source contains duplicate ${column}: ${value}.`)
    result.set(value, row)
  }
  return result
}

export function shapeFacilityWorkBacklog(
  facilities: readonly DomainRow[], equipment: readonly DomainRow[], workOrders: readonly DomainRow[],
): FacilityWorkBacklog {
  const facilityIndex = uniqueIndex(facilities, 'facility_id')
  const equipmentIndex = uniqueIndex(equipment, 'equipment_id')
  const open = [...uniqueIndex(workOrders, 'workOrderNumber').values()].filter(order =>
    !DOMAIN_SEMANTICS.open_work.excluded_statuses_case_insensitive
      .includes(requiredString(order, 'status').toLowerCase() as 'completed' | 'cancelled'))
  const counts = new Map<string, { equipment: Set<string>; orders: number }>(
    [...facilityIndex.keys()].map(facilityId =>
      [facilityId, { equipment: new Set<string>(), orders: 0 }]),
  )
  const unmatched: FacilityWorkBacklog['unmatched'] = []
  for (const order of open) {
    const equipmentId = requiredString(order, 'equipmentId')
    const facilityId = equipmentIndex.get(equipmentId)?.facility_id
    const resolved = typeof facilityId === 'string' && facilityIndex.has(facilityId)
    const bucketId = resolved ? facilityId : 'UNMAPPED'
    const bucket = counts.get(bucketId) ?? { equipment: new Set<string>(), orders: 0 }
    bucket.equipment.add(equipmentId)
    bucket.orders += 1
    counts.set(bucketId, bucket)
    if (!resolved) {
      unmatched.push({
        workOrderNumber: requiredString(order, 'workOrderNumber'),
        equipmentId,
        reason: typeof facilityId === 'string'
          ? `Facility ${facilityId} was not returned by the complete facility inventory.`
          : 'Equipment or its facility identity was not returned by the complete equipment inventory.',
      })
    }
  }
  return {
    group_by: 'facility',
    rows: [...counts.entries()].map(([facilityId, count]) => ({
      facility_id: facilityId,
      facility_name: facilityId === 'UNMAPPED'
        ? null
        : typeof facilityIndex.get(facilityId)?.facility_name === 'string'
          ? facilityIndex.get(facilityId)!.facility_name as string
          : null,
      equipment_with_open_work: count.equipment.size,
      open_work_order_count: count.orders,
    })),
    open_work_orders: open,
    unmatched,
    semantics: 'Complete direct SQL work inventory excluding Completed and Cancelled statuses, joined by '
      + 'work_orders.equipmentId = equipment.equipment_id and equipment.facility_id = facilities.facility_id.',
  }
}

export type EquipmentWorkBacklog = {
  group_by: 'equipment'
  rows: Array<{
    equipment_id: string
    tag: string | null
    facility_id: string | null
    facility_name: string | null
    open_work_order_count: number
  }>
  open_work_orders: DomainRow[]
  unmatched: FacilityWorkBacklog['unmatched']
  semantics: string
}

export function shapeEquipmentWorkBacklog(
  facilities: readonly DomainRow[], equipment: readonly DomainRow[], workOrders: readonly DomainRow[],
): EquipmentWorkBacklog {
  const facilityBacklog = shapeFacilityWorkBacklog(facilities, equipment, workOrders)
  const facilityIndex = uniqueIndex(facilities, 'facility_id')
  const equipmentIndex = uniqueIndex(equipment, 'equipment_id')
  const counts = new Map([...equipmentIndex.keys()].map(equipmentId => [equipmentId, 0]))
  for (const order of facilityBacklog.open_work_orders) {
    const equipmentId = requiredString(order, 'equipmentId')
    counts.set(equipmentId, (counts.get(equipmentId) ?? 0) + 1)
  }
  return {
    group_by: 'equipment',
    rows: [...counts.entries()].map(([equipmentId, count]) => {
      const asset = equipmentIndex.get(equipmentId)
      const facilityId = typeof asset?.facility_id === 'string' ? asset.facility_id : null
      return {
        equipment_id: equipmentId,
        tag: typeof asset?.tag === 'string' ? asset.tag : null,
        facility_id: facilityId,
        facility_name: facilityId && typeof facilityIndex.get(facilityId)?.facility_name === 'string'
          ? facilityIndex.get(facilityId)!.facility_name as string
          : null,
        open_work_order_count: count,
      }
    }),
    open_work_orders: facilityBacklog.open_work_orders,
    unmatched: facilityBacklog.unmatched,
    semantics: 'Complete direct SQL work inventory excluding Completed and Cancelled statuses, grouped by '
      + 'work_orders.equipmentId and enriched through equipment.equipment_id and equipment.facility_id.',
  }
}

export function shapeWorkBacklog(
  groupBy: unknown, facilities: readonly DomainRow[], equipment: readonly DomainRow[],
  workOrders: readonly DomainRow[],
): FacilityWorkBacklog | EquipmentWorkBacklog {
  if (groupBy === 'facility') return shapeFacilityWorkBacklog(facilities, equipment, workOrders)
  if (groupBy === 'equipment') return shapeEquipmentWorkBacklog(facilities, equipment, workOrders)
  throw new Error('Work backlog requires group_by equipment or facility.')
}

export function validateDomainContract(): void {
  const entities = new Map<string, { columns: readonly { name: string }[] }>([
    ...ASSET_ENTITIES.map(entity => [`lakehouse:${entity.key}`, entity] as const),
    ...OPERATIONS_ENTITIES.map(entity => [`sql:${entity.key}`, entity] as const),
    ...KUSTO_SOURCES.map(source => [`eventhouse:${source.name}`, {
      columns: source.name === 'OPCUAEvents'
        ? [{ name: 'event_time' }, { name: 'opcua_node_id' }, { name: 'value' }, { name: 'quality' }]
        : [],
    }] as const),
  ])
  for (const relationship of DOMAIN_RELATIONSHIPS) {
    for (const endpoint of [relationship.from, relationship.to]) {
      const entity = entities.get(`${endpoint.source}:${endpoint.entity}`)
      if (!entity || !entity.columns.some(column => column.name === endpoint.column)) {
        throw new Error(`Invalid domain relationship endpoint: ${relationship.key} ${endpoint.entity}.${endpoint.column}.`)
      }
    }
  }
}

validateDomainContract()
