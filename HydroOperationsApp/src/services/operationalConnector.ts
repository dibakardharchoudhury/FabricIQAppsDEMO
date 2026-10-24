import type { ConnectorConfig } from '@microsoft/rayfin-connectors'
import type { HydroOperationsSchema } from '../../rayfin/connectors/hydrooperations/schema'

export type OperationalConnectors = { hydrooperations: HydroOperationsSchema }

export const operationalConnectorConfig = {
  connector: 'fabric-sqldatabase',
  operations: ['read', 'create', 'update', 'delete'],
  entities: {
    WorkOrder: ['id', 'workOrderNumber', 'equipmentId', 'instrumentId', 'opcuaNodeId', 'title', 'description',
      'priority', 'status', 'assignedToOid', 'createdByOid', 'createdAt', 'dueAt', 'completedAt'],
    MaintenanceNotification: ['id', 'equipmentId', 'opcuaNodeId', 'summary', 'severity', 'status', 'reportedByOid', 'reportedAt'],
    Inspection: ['id', 'equipmentId', 'opcuaNodeId', 'inspectionType', 'result', 'findings', 'inspectorOid', 'inspectedAt', 'nextDueAt'],
    SparePart: ['id', 'partNumber', 'name', 'category', 'equipmentType', 'quantityOnHand', 'reorderLevel',
      'storageLocation', 'unitCostUsd', 'lastRestockedAt'],
    Asset3DModel: ['id', 'equipmentId', 'modelName', 'format', 'modelUrl', 'thumbnailUrl', 'fileSizeMb',
      'version', 'updatedByOid', 'updatedAt'],
  },
} as const satisfies ConnectorConfig
