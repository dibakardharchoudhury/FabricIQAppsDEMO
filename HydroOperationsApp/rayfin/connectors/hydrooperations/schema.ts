import { authenticated, date, decimal, entity, int, text, uuid } from '@microsoft/rayfin-core'
import { Source } from '@microsoft/rayfin-connectors'
import type { ConnectorConfig } from '@microsoft/rayfin-connectors'
import type { GraphQLBackedConnector } from '@microsoft/rayfin-connector-fabric-graphql'
import type { EntityClass } from '@microsoft/rayfin-core/schema'

@entity()
@authenticated('*')
export class WorkOrder extends Source({ schema: 'dbo', table: 'WorkOrders', primaryKey: ['id'] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @uuid() id!: string
  @text() workOrderNumber!: string
  @text() equipmentId!: string
  @text({ optional: true }) instrumentId?: string
  @text({ optional: true }) opcuaNodeId?: string
  @text() title!: string
  @text({ optional: true }) description?: string
  @text() priority!: string
  @text() status!: string
  @text({ optional: true }) assignedToOid?: string
  @text() createdByOid!: string
  @date() createdAt!: Date
  @date({ optional: true }) dueAt?: Date
  @date({ optional: true }) completedAt?: Date
}

@entity()
@authenticated('*')
export class MaintenanceNotification extends Source({ schema: 'dbo', table: 'MaintenanceNotifications', primaryKey: ['id'] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @uuid() id!: string
  @text() equipmentId!: string
  @text({ optional: true }) opcuaNodeId?: string
  @text() summary!: string
  @text() severity!: string
  @text() status!: string
  @text() reportedByOid!: string
  @date() reportedAt!: Date
}

@entity()
@authenticated('*')
export class Inspection extends Source({ schema: 'dbo', table: 'Inspections', primaryKey: ['id'] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @uuid() id!: string
  @text() equipmentId!: string
  @text({ optional: true }) opcuaNodeId?: string
  @text() inspectionType!: string
  @text() result!: string
  @text({ optional: true }) findings?: string
  @text() inspectorOid!: string
  @date() inspectedAt!: Date
  @date({ optional: true }) nextDueAt?: Date
}

@entity()
@authenticated('*')
export class Asset3DModel extends Source({ schema: 'dbo', table: 'Asset3DModels', primaryKey: ['id'] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @uuid() id!: string
  @text() equipmentId!: string
  @text() modelName!: string
  @text() format!: string
  @text() modelUrl!: string
  @text({ optional: true }) thumbnailUrl?: string
  @decimal({ optional: true }) fileSizeMb?: number
  @text({ optional: true }) version?: string
  @text() updatedByOid!: string
  @date() updatedAt!: Date
}

@entity()
@authenticated('*')
export class SparePart extends Source({ schema: 'dbo', table: 'SpareParts', primaryKey: ['id'] }) {
  declare static [Symbol.metadata]: EntityClass[typeof Symbol.metadata]
  @uuid() id!: string
  @text({ max: 255 }) partNumber!: string
  @text() name!: string
  @text() category!: string
  @text() equipmentType!: string
  @int() quantityOnHand!: number
  @int() reorderLevel!: number
  @text() storageLocation!: string
  @decimal({ optional: true }) unitCostUsd?: number
  @date({ optional: true }) lastRestockedAt?: Date
}

export const connectorConfig = {
  connector: 'fabric-sqldatabase',
  operations: ['read', 'create', 'update', 'delete'],
  entities: { WorkOrder, Inspection, SparePart, Asset3DModel, MaintenanceNotification },
} as const satisfies ConnectorConfig

export type HydroOperationsSchema = GraphQLBackedConnector<typeof connectorConfig.entities, typeof connectorConfig>
