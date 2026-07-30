-- Idempotent sample data for app-owned mutable records only.
-- All equipment/instrument/signal keys resolve to existing STID Lakehouse rows.
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRANSACTION;

DECLARE @ActorOid nvarchar(200) = '$(ActorOid)';
IF @ActorOid = '$' + '(ActorOid)' OR NULLIF(@ActorOid, '') IS NULL
    SET @ActorOid = '00000000-0000-0000-0000-000000000000';

MERGE dbo.WorkOrders AS target
USING (VALUES
    ('10000000-0000-0000-0000-000000000001','WO-2841','EQUIP_RTI_T001','INST_T001_VIBRATION_A','ns=2;s=T001.vibration_a','Inspect guide bearing vibration','Inspect bearing, alignment, and lubrication after elevated vibration trend.','Critical','In progress',@ActorOid,@ActorOid,'2026-07-28T08:00:00','2026-08-01T16:00:00'),
    ('10000000-0000-0000-0000-000000000002','WO-2836','EQUIP_RTI_T002','INST_T002_INLET_PRESSURE','ns=2;s=T002.inlet_pressure','Review inlet pressure variance','Validate transducer and compare against local gauge.','High','Scheduled',@ActorOid,@ActorOid,'2026-07-27T10:00:00','2026-08-03T12:00:00'),
    ('10000000-0000-0000-0000-000000000003','WO-2829','EQUIP_RTI_T003','INST_T003_TURBINE_TEMP','ns=2;s=T003.turbine_temp','Inspect turbine cooling circuit','Check strainers, flow, and temperature sensor calibration.','Medium','Ready',NULL,@ActorOid,'2026-07-26T09:30:00','2026-08-05T12:00:00')
) AS source(id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)
ON target.workOrderNumber = source.workOrderNumber
WHEN MATCHED THEN UPDATE SET title=source.title, description=source.description, priority=source.priority, status=source.status, assignedToOid=source.assignedToOid, dueAt=source.dueAt
WHEN NOT MATCHED THEN INSERT (id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)
VALUES (source.id,source.workOrderNumber,source.equipmentId,source.instrumentId,source.opcuaNodeId,source.title,source.description,source.priority,source.status,source.assignedToOid,source.createdByOid,source.createdAt,source.dueAt);

MERGE dbo.MaintenanceNotifications AS target
USING (VALUES
    ('20000000-0000-0000-0000-000000000001','EQUIP_RTI_T001','ns=2;s=T001.vibration_a','Guide bearing vibration exceeded advisory threshold','Warning','Open',@ActorOid,'2026-07-30T07:42:00'),
    ('20000000-0000-0000-0000-000000000002','EQUIP_RTI_T002','ns=2;s=T002.inlet_pressure','Inlet pressure variance requires review','Advisory','Open',@ActorOid,'2026-07-30T06:15:00')
) AS source(id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET summary=source.summary,severity=source.severity,status=source.status
WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)
VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.summary,source.severity,source.status,source.reportedByOid,source.reportedAt);

MERGE dbo.OperatorNotes AS target
USING (VALUES
    ('30000000-0000-0000-0000-000000000001','EQUIP_RTI_T001','ns=2;s=T001.vibration_a','Vibration increased during ramp to peak load; no audible bearing noise.',@ActorOid,'2026-07-30T07:55:00')
) AS source(id,equipmentId,opcuaNodeId,body,authorOid,createdAt)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET body=source.body
WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,body,authorOid,createdAt)
VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.body,source.authorOid,source.createdAt);

MERGE dbo.ShiftHandovers AS target
USING (VALUES
    ('50000000-0000-0000-0000-000000000001','2026-07-30T06:00:00','2026-07-30T18:00:00','Unit 01 vibration advisory remains active. WO-2841 is in progress; avoid rapid load changes pending inspection.',@ActorOid,NULL,0,NULL)
) AS source(id,shiftStart,shiftEnd,summary,outgoingOperatorOid,incomingOperatorOid,accepted,acceptedAt)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET summary=source.summary
WHEN NOT MATCHED THEN INSERT (id,shiftStart,shiftEnd,summary,outgoingOperatorOid,incomingOperatorOid,accepted,acceptedAt)
VALUES (source.id,source.shiftStart,source.shiftEnd,source.summary,source.outgoingOperatorOid,source.incomingOperatorOid,source.accepted,source.acceptedAt);

COMMIT TRANSACTION;

SELECT 'WorkOrder' entity, COUNT(*) row_count FROM dbo.WorkOrders
UNION ALL SELECT 'MaintenanceNotification', COUNT(*) FROM dbo.MaintenanceNotifications
UNION ALL SELECT 'OperatorNote', COUNT(*) FROM dbo.OperatorNotes
UNION ALL SELECT 'ShiftHandover', COUNT(*) FROM dbo.ShiftHandovers;