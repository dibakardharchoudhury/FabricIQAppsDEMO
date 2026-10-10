import { ASSET_ENTITIES, KUSTO_SOURCES, OPERATIONS_ENTITIES } from './catalog.ts'

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
} as const

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
