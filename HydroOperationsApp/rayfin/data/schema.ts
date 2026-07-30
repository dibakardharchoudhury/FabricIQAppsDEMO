import { authenticated, boolean, date, entity, text, uuid } from '@microsoft/rayfin-core'

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

@entity()
@authenticated('*')
export class AlarmAcknowledgement {
  @uuid() id!: string
  @text() opcuaNodeId!: string
  @date() alarmEventTime!: Date
  @text() acknowledgedByOid!: string
  @date() acknowledgedAt!: Date
  @text({ optional: true }) note?: string
}

@entity()
@authenticated('*')
export class OperatorNote {
  @uuid() id!: string
  @text() equipmentId!: string
  @text({ optional: true }) opcuaNodeId?: string
  @text() body!: string
  @text() authorOid!: string
  @date() createdAt!: Date
}

@entity()
@authenticated('*')
export class StreamRun {
  @uuid() id!: string
  @text() pipelineItemId!: string
  @text() requestedByOid!: string
  @date() requestedAt!: Date
  @text() status!: string
  @text({ optional: true }) fabricJobInstanceId?: string
}

@entity()
@authenticated('*')
export class ShiftHandover {
  @uuid() id!: string
  @date() shiftStart!: Date
  @date() shiftEnd!: Date
  @text() summary!: string
  @text() outgoingOperatorOid!: string
  @text({ optional: true }) incomingOperatorOid?: string
  @boolean() accepted!: boolean
  @date({ optional: true }) acceptedAt?: Date
}