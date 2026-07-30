import { authenticated, date, decimal, entity, int, text, uuid } from '@microsoft/rayfin-core'

@entity()
@authenticated('*')
export class WorkOrder {
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
export class MaintenanceNotification {
  @uuid() id!: string
  @text() equipmentId!: string
  @text({ optional: true }) opcuaNodeId?: string
  @text() summary!: string
  @text() severity!: string
  @text() status!: string
  @text() reportedByOid!: string
  @date() reportedAt!: Date
}

// Digital twin / 3D visualization asset registered against a piece of equipment.
// The web app surfaces these next to the live STID + telemetry view for each asset.
@entity()
@authenticated('*')
export class Asset3DModel {
  @uuid() id!: string
  @text() equipmentId!: string
  @text() modelName!: string
  @text() format!: string // GLB, IFC, OBJ, USDZ
  @text() modelUrl!: string
  @text({ optional: true }) thumbnailUrl?: string
  @decimal({ optional: true }) fileSizeMb?: number
  @text({ optional: true }) version?: string
  @text() updatedByOid!: string
  @date() updatedAt!: Date
}

// Field / condition inspection record for an asset (visual, thermographic, vibration, etc.).
@entity()
@authenticated('*')
export class Inspection {
  @uuid() id!: string
  @text() equipmentId!: string
  @text({ optional: true }) opcuaNodeId?: string
  @text() inspectionType!: string // VISUAL, THERMOGRAPHIC, VIBRATION, LUBRICATION
  @text() result!: string // PASS, ATTENTION, FAIL
  @text({ optional: true }) findings?: string
  @text() inspectorOid!: string
  @date() inspectedAt!: Date
  @date({ optional: true }) nextDueAt?: Date
}

// Spare part inventory available to service the fleet. Drives maintenance readiness.
@entity()
@authenticated('*')
export class SparePart {
  @uuid() id!: string
  @text({ unique: true, max: 255 }) partNumber!: string
  @text() name!: string
  @text() category!: string // BEARING, SEAL, SENSOR, VALVE, ELECTRICAL
  @text() equipmentType!: string // TURB
  @int() quantityOnHand!: number
  @int() reorderLevel!: number
  @text() storageLocation!: string
  @decimal({ optional: true }) unitCostUsd?: number
  @date({ optional: true }) lastRestockedAt?: Date
}