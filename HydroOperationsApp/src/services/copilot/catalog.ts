// The allow-list of data the Foundry copilot may read. This is the governance boundary:
// nothing outside this catalog is reachable by a tool, and the same text is rendered into
// the system prompt as the schema the model plans against.

export type CatalogColumn = { name: string; description?: string }

export type CatalogEntity = {
  key: string
  source: 'lakehouse' | 'sql'
  physicalName: string
  description: string
  columns: CatalogColumn[]
}

export const ASSET_ENTITIES: CatalogEntity[] = [
  {
    key: 'facilities',
    source: 'lakehouse',
    physicalName: 'silver_facilities',
    description: 'Hydro power stations.',
    columns: [
      { name: 'facility_id' }, { name: 'facility_name' }, { name: 'type' }, { name: 'country' },
      { name: 'lat' }, { name: 'lon' }, { name: 'commissioned_date' },
    ],
  },
  {
    key: 'equipment',
    source: 'lakehouse',
    physicalName: 'silver_equipments',
    description: 'Turbines and other equipment installed at a facility.',
    columns: [
      { name: 'equipment_id' }, { name: 'facility_id' }, { name: 'system_id' },
      { name: 'equipment_type_code' }, { name: 'equipment_type_name' }, { name: 'tag' },
      { name: 'manufacturer' }, { name: 'model' },
      { name: 'criticality', description: 'Integer; higher means more critical.' },
      { name: 'install_date' }, { name: 'status' }, { name: 'is_active' },
    ],
  },
  {
    key: 'instruments',
    source: 'lakehouse',
    physicalName: 'silver_instruments',
    description: 'Sensors attached to equipment. Join to telemetry on opcua_node_id.',
    columns: [
      { name: 'opcua_node_id', description: 'Join key to the OPCUAEvents telemetry table.' },
      { name: 'tag' }, { name: 'instrument_id' }, { name: 'equipment_id' }, { name: 'system_id' },
      { name: 'facility_id' }, { name: 'unit' }, { name: 'instrument_type' }, { name: 'is_active' },
    ],
  },
]

export const OPERATIONS_ENTITIES: CatalogEntity[] = [
  {
    key: 'work_orders',
    source: 'sql',
    physicalName: 'WorkOrder',
    description: 'Maintenance work orders raised against equipment.',
    columns: [
      { name: 'id' }, { name: 'workOrderNumber' }, { name: 'equipmentId' }, { name: 'instrumentId' },
      { name: 'opcuaNodeId' }, { name: 'title' }, { name: 'description' }, { name: 'priority' },
      { name: 'status' }, { name: 'createdAt' }, { name: 'dueAt' }, { name: 'completedAt' },
    ],
  },
  {
    key: 'inspections',
    source: 'sql',
    physicalName: 'Inspection',
    description: 'Completed inspections and their findings.',
    columns: [
      { name: 'id' }, { name: 'equipmentId' }, { name: 'opcuaNodeId' }, { name: 'inspectionType' },
      { name: 'result' }, { name: 'findings' }, { name: 'inspectedAt' }, { name: 'nextDueAt' },
    ],
  },
  {
    key: 'spare_parts',
    source: 'sql',
    physicalName: 'SparePart',
    description: 'Spare part stock levels.',
    columns: [
      { name: 'id' }, { name: 'partNumber' }, { name: 'name' }, { name: 'category' },
      { name: 'equipmentType' }, { name: 'quantityOnHand' }, { name: 'reorderLevel' },
      { name: 'storageLocation' }, { name: 'unitCostUsd' }, { name: 'lastRestockedAt' },
    ],
  },
  {
    key: 'notifications',
    source: 'sql',
    physicalName: 'MaintenanceNotification',
    description: 'Operator-raised maintenance notifications.',
    columns: [
      { name: 'id' }, { name: 'equipmentId' }, { name: 'opcuaNodeId' }, { name: 'summary' },
      { name: 'severity' }, { name: 'status' }, { name: 'reportedAt' },
    ],
  },
]

/** Kusto tables and functions the copilot may read. Anything else is rejected by the validator. */
export const KUSTO_SOURCES = [
  {
    name: 'OPCUAEvents',
    description: 'Raw OPC UA telemetry. Columns: event_time (datetime), opcua_node_id (string), value (real), quality (string: GOOD|UNCERTAIN|BAD).',
  },
  {
    name: 'AssetMaster',
    description: 'Function AssetMaster(). Instruments joined to equipment and facilities: station, turbine, sensor_group, unit, opcua_node_id.',
  },
  {
    name: 'TelemetryEnriched',
    description: 'Function TelemetryEnriched(start:datetime, end:datetime, stations:dynamic, turbines:dynamic). Telemetry pre-joined to asset master. Pass dynamic(null) for stations/turbines to include all.',
  },
] as const

export const KUSTO_SOURCE_NAMES: string[] = KUSTO_SOURCES.map(source => source.name)

export const ASSET_ENTITY_KEYS = ASSET_ENTITIES.map(entity => entity.key)
export const OPERATIONS_ENTITY_KEYS = OPERATIONS_ENTITIES.map(entity => entity.key)

function describe(entities: CatalogEntity[]): string {
  return entities
    .map(entity => {
      const columns = entity.columns
        .map(column => column.description ? `${column.name} (${column.description})` : column.name)
        .join(', ')
      return `- ${entity.key} [${entity.physicalName}]: ${entity.description} Columns: ${columns}`
    })
    .join('\n')
}

/** The schema block injected into the system prompt. Derived from the catalog so they cannot drift. */
export function catalogPrompt(): string {
  return [
    'Asset metadata (Lakehouse, tool: query_assets):',
    describe(ASSET_ENTITIES),
    '',
    'Operational records (SQL, tool: query_operations):',
    describe(OPERATIONS_ENTITIES),
    '',
    'Telemetry (Kusto/Eventhouse, tools: query_telemetry and run_kql):',
    KUSTO_SOURCES.map(source => `- ${source.name}: ${source.description}`).join('\n'),
  ].join('\n')
}
