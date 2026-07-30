-- Idempotent sample operational data for the Hydro Operations app (3 facilities).
-- App-owned mutable records only. Every equipmentId / opcua_node_id resolves to an
-- existing STID Lakehouse row seeded by RTI_001_create_lakehouse_SelfContained.
-- Regenerate with: python _gen_seed.py  (repo root).
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
    ('10000000-0000-0000-0000-000000000003','WO-2829','EQUIP_RTI_T003','INST_T003_TURBINE_TEMP','ns=2;s=T003.turbine_temp','Inspect turbine cooling circuit','Check strainers, flow, and temperature sensor calibration.','Medium','Ready',NULL,@ActorOid,'2026-07-26T09:30:00','2026-08-05T12:00:00'),
    ('10000000-0000-0000-0000-000000000004','WO-2848','EQUIP_RTI_T006','INST_T006_VIBRATION_D','ns=2;s=T006.vibration_d','Fjord unit driven-end vibration check','Trend review and balancing assessment on driven-end bearing.','High','Approved',@ActorOid,@ActorOid,'2026-07-29T07:00:00','2026-08-04T16:00:00'),
    ('10000000-0000-0000-0000-000000000005','WO-2851','EQUIP_RTI_T008','INST_T008_POWER_OUTPUT','ns=2;s=T008.power_output','Investigate power output dip','Correlate output with head and guide-vane position.','Medium','Planned',NULL,@ActorOid,'2026-07-29T13:20:00','2026-08-06T12:00:00'),
    ('10000000-0000-0000-0000-000000000006','WO-2853','EQUIP_RTI_T010','INST_T010_TURBINE_SPEED','ns=2;s=T010.turbine_speed','Overspeed trip test','Scheduled governor overspeed protection test.','Low','Scheduled',@ActorOid,@ActorOid,'2026-07-30T09:00:00','2026-08-08T12:00:00'),
    ('10000000-0000-0000-0000-000000000007','WO-2857','EQUIP_RTI_T011','INST_T011_TURBINE_TEMP','ns=2;s=T011.turbine_temp','Highland unit cooling inspection','Inspect heat exchanger fouling after temperature rise.','High','In progress',@ActorOid,@ActorOid,'2026-07-30T11:00:00','2026-08-02T18:00:00'),
    ('10000000-0000-0000-0000-000000000008','WO-2860','EQUIP_RTI_T013','INST_T013_VIBRATION_A','ns=2;s=T013.vibration_a','Highland unit bearing endoscopy','Borescope guide bearing following vibration advisory.','Critical','Ready',NULL,@ActorOid,'2026-07-30T14:10:00','2026-08-03T12:00:00'),
    ('10000000-0000-0000-0000-000000000009','WO-2862','EQUIP_RTI_T015','INST_T015_INLET_PRESSURE','ns=2;s=T015.inlet_pressure','Inlet strainer differential check','Verify strainer differential pressure and clean if required.','Medium','Approved',@ActorOid,@ActorOid,'2026-07-31T08:30:00','2026-08-09T12:00:00'),
    ('10000000-0000-0000-0000-000000000010','WO-2820','EQUIP_RTI_T004','INST_T004_TURBINE_TEMP','ns=2;s=T004.turbine_temp','Annual thermographic survey','Completed annual thermographic survey; no anomalies.','Low','Completed',@ActorOid,@ActorOid,'2026-07-15T08:00:00','2026-07-20T12:00:00'),
    ('10000000-0000-0000-0000-000000000011','WO-2812','EQUIP_RTI_T007','INST_T007_VIBRATION_A','ns=2;s=T007.vibration_a','Bearing lubrication refresh','Completed lubrication service on guide bearing.','Medium','Completed',@ActorOid,@ActorOid,'2026-07-10T08:00:00','2026-07-14T12:00:00'),
    ('10000000-0000-0000-0000-000000000012','WO-2867','EQUIP_RTI_T012','INST_T012_POWER_OUTPUT','ns=2;s=T012.power_output','Efficiency test after overhaul','Post-overhaul efficiency verification test.','High','Draft',NULL,@ActorOid,'2026-07-31T15:00:00','2026-08-12T12:00:00')
) AS source(id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)
ON target.workOrderNumber = source.workOrderNumber
WHEN MATCHED THEN UPDATE SET title=source.title, description=source.description, priority=source.priority, status=source.status, assignedToOid=source.assignedToOid, dueAt=source.dueAt
WHEN NOT MATCHED THEN INSERT (id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)
VALUES (source.id,source.workOrderNumber,source.equipmentId,source.instrumentId,source.opcuaNodeId,source.title,source.description,source.priority,source.status,source.assignedToOid,source.createdByOid,source.createdAt,source.dueAt);

MERGE dbo.MaintenanceNotifications AS target
USING (VALUES
    ('20000000-0000-0000-0000-000000000001','EQUIP_RTI_T001','ns=2;s=T001.vibration_a','Guide bearing vibration exceeded advisory threshold','Warning','Open',@ActorOid,'2026-07-30T07:42:00'),
    ('20000000-0000-0000-0000-000000000002','EQUIP_RTI_T002','ns=2;s=T002.inlet_pressure','Inlet pressure variance requires review','Advisory','Open',@ActorOid,'2026-07-30T06:15:00'),
    ('20000000-0000-0000-0000-000000000003','EQUIP_RTI_T006','ns=2;s=T006.vibration_d','Driven-end vibration trending upward','Warning','Open',@ActorOid,'2026-07-29T18:05:00'),
    ('20000000-0000-0000-0000-000000000004','EQUIP_RTI_T008','ns=2;s=T008.power_output','Power output below expected for head','Advisory','Acknowledged',@ActorOid,'2026-07-29T12:40:00'),
    ('20000000-0000-0000-0000-000000000005','EQUIP_RTI_T011','ns=2;s=T011.turbine_temp','Bearing temperature above normal band','Warning','Open',@ActorOid,'2026-07-30T10:20:00'),
    ('20000000-0000-0000-0000-000000000006','EQUIP_RTI_T013','ns=2;s=T013.vibration_a','Vibration advisory on guide bearing','Critical','Open',@ActorOid,'2026-07-30T13:55:00')
) AS source(id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET summary=source.summary,severity=source.severity,status=source.status
WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)
VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.summary,source.severity,source.status,source.reportedByOid,source.reportedAt);

MERGE dbo.Inspections AS target
USING (VALUES
    ('60000000-0000-0000-0000-000000000001','EQUIP_RTI_T001',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000002','EQUIP_RTI_T001','ns=2;s=T001.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000003','EQUIP_RTI_T002',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000004','EQUIP_RTI_T002','ns=2;s=T002.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000005','EQUIP_RTI_T003',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000006','EQUIP_RTI_T003',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000007','EQUIP_RTI_T004',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000008','EQUIP_RTI_T004','ns=2;s=T004.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000009','EQUIP_RTI_T005',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000010','EQUIP_RTI_T005','ns=2;s=T005.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000011','EQUIP_RTI_T006',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000012','EQUIP_RTI_T006',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000013','EQUIP_RTI_T007',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000014','EQUIP_RTI_T007','ns=2;s=T007.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000015','EQUIP_RTI_T008',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-27T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000016','EQUIP_RTI_T008','ns=2;s=T008.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-27T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000017','EQUIP_RTI_T009',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000018','EQUIP_RTI_T009',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-20T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000019','EQUIP_RTI_T010',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000020','EQUIP_RTI_T010','ns=2;s=T010.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-21T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000021','EQUIP_RTI_T011',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000022','EQUIP_RTI_T011','ns=2;s=T011.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-22T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000023','EQUIP_RTI_T012',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000024','EQUIP_RTI_T012',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-23T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000025','EQUIP_RTI_T013',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000026','EQUIP_RTI_T013','ns=2;s=T013.turbine_temp','THERMOGRAPHIC','PASS','No hot spots detected on bearings or windings.',@ActorOid,'2026-07-24T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000027','EQUIP_RTI_T014',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000028','EQUIP_RTI_T014','ns=2;s=T014.vibration_a','VIBRATION','ATTENTION','Guide bearing vibration slightly above baseline; monitor.',@ActorOid,'2026-07-25T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000029','EQUIP_RTI_T015',NULL,'VISUAL','PASS','Casing, fasteners, and seals visually normal.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00'),
    ('60000000-0000-0000-0000-000000000030','EQUIP_RTI_T015',NULL,'LUBRICATION','PASS','Oil level and quality within specification.',@ActorOid,'2026-07-26T09:00:00','2026-10-15T09:00:00')
) AS source(id,equipmentId,opcuaNodeId,inspectionType,result,findings,inspectorOid,inspectedAt,nextDueAt)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET inspectionType=source.inspectionType,result=source.result,findings=source.findings,inspectedAt=source.inspectedAt,nextDueAt=source.nextDueAt
WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,inspectionType,result,findings,inspectorOid,inspectedAt,nextDueAt)
VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.inspectionType,source.result,source.findings,source.inspectorOid,source.inspectedAt,source.nextDueAt);

MERGE dbo.SpareParts AS target
USING (VALUES
    ('70000000-0000-0000-0000-000000000001','SP-BRG-1001','Guide bearing pad set','BEARING','TURB',6,4,'Warehouse A-01',4200.0,'2026-06-10'),
    ('70000000-0000-0000-0000-000000000002','SP-BRG-1002','Thrust bearing segment','BEARING','TURB',2,3,'Warehouse A-01',5100.0,'2026-05-22'),
    ('70000000-0000-0000-0000-000000000003','SP-SEAL-2001','Main shaft seal kit','SEAL','TURB',9,5,'Warehouse A-02',880.0,'2026-06-28'),
    ('70000000-0000-0000-0000-000000000004','SP-SEAL-2002','Guide vane seal ring','SEAL','TURB',3,4,'Warehouse A-02',640.0,'2026-04-30'),
    ('70000000-0000-0000-0000-000000000005','SP-SEN-3001','Vibration probe (eddy current)','SENSOR','TURB',12,6,'Store B-11',1350.0,'2026-07-05'),
    ('70000000-0000-0000-0000-000000000006','SP-SEN-3002','RTD temperature sensor','SENSOR','TURB',20,8,'Store B-11',210.0,'2026-07-12'),
    ('70000000-0000-0000-0000-000000000007','SP-SEN-3003','Pressure transducer','SENSOR','TURB',7,6,'Store B-12',490.0,'2026-06-18'),
    ('70000000-0000-0000-0000-000000000008','SP-VAL-4001','Guide vane servo valve','VALVE','TURB',2,2,'Warehouse A-03',7300.0,'2026-03-15'),
    ('70000000-0000-0000-0000-000000000009','SP-VAL-4002','Cooling water control valve','VALVE','TURB',5,3,'Warehouse A-03',1120.0,'2026-06-02'),
    ('70000000-0000-0000-0000-000000000010','SP-ELE-5001','Excitation control card','ELECTRICAL','TURB',4,3,'Store B-20',2650.0,'2026-05-09'),
    ('70000000-0000-0000-0000-000000000011','SP-ELE-5002','Governor PLC module','ELECTRICAL','TURB',1,2,'Store B-20',3900.0,'2026-02-27'),
    ('70000000-0000-0000-0000-000000000012','SP-ELE-5003','Field cabling loom','ELECTRICAL','TURB',14,6,'Store B-21',180.0,'2026-07-20')
) AS source(id,partNumber,name,category,equipmentType,quantityOnHand,reorderLevel,storageLocation,unitCostUsd,lastRestockedAt)
ON target.partNumber = source.partNumber
WHEN MATCHED THEN UPDATE SET name=source.name,category=source.category,quantityOnHand=source.quantityOnHand,reorderLevel=source.reorderLevel,storageLocation=source.storageLocation,unitCostUsd=source.unitCostUsd,lastRestockedAt=source.lastRestockedAt
WHEN NOT MATCHED THEN INSERT (id,partNumber,name,category,equipmentType,quantityOnHand,reorderLevel,storageLocation,unitCostUsd,lastRestockedAt)
VALUES (source.id,source.partNumber,source.name,source.category,source.equipmentType,source.quantityOnHand,source.reorderLevel,source.storageLocation,source.unitCostUsd,source.lastRestockedAt);

MERGE dbo.Asset3DModels AS target
USING (VALUES
    ('80000000-0000-0000-0000-000000000001','EQUIP_RTI_T001','Turbine T001 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,9.5,'v1.1',@ActorOid,'2026-07-11T12:00:00'),
    ('80000000-0000-0000-0000-000000000002','EQUIP_RTI_T002','Turbine T002 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,11.0,'v1.2',@ActorOid,'2026-07-12T12:00:00'),
    ('80000000-0000-0000-0000-000000000003','EQUIP_RTI_T003','Turbine T003 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,12.5,'v1.3',@ActorOid,'2026-07-13T12:00:00'),
    ('80000000-0000-0000-0000-000000000004','EQUIP_RTI_T004','Turbine T004 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,14.0,'v1.4',@ActorOid,'2026-07-14T12:00:00'),
    ('80000000-0000-0000-0000-000000000005','EQUIP_RTI_T005','Turbine T005 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,8.0,'v1.5',@ActorOid,'2026-07-15T12:00:00'),
    ('80000000-0000-0000-0000-000000000006','EQUIP_RTI_T006','Turbine T006 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,9.5,'v1.6',@ActorOid,'2026-07-16T12:00:00'),
    ('80000000-0000-0000-0000-000000000007','EQUIP_RTI_T007','Turbine T007 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,11.0,'v1.7',@ActorOid,'2026-07-17T12:00:00'),
    ('80000000-0000-0000-0000-000000000008','EQUIP_RTI_T008','Turbine T008 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,12.5,'v1.8',@ActorOid,'2026-07-18T12:00:00'),
    ('80000000-0000-0000-0000-000000000009','EQUIP_RTI_T009','Turbine T009 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,14.0,'v1.9',@ActorOid,'2026-07-19T12:00:00'),
    ('80000000-0000-0000-0000-000000000010','EQUIP_RTI_T010','Turbine T010 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,8.0,'v1.10',@ActorOid,'2026-07-20T12:00:00'),
    ('80000000-0000-0000-0000-000000000011','EQUIP_RTI_T011','Turbine T011 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,9.5,'v1.11',@ActorOid,'2026-07-21T12:00:00'),
    ('80000000-0000-0000-0000-000000000012','EQUIP_RTI_T012','Turbine T012 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,11.0,'v1.12',@ActorOid,'2026-07-22T12:00:00'),
    ('80000000-0000-0000-0000-000000000013','EQUIP_RTI_T013','Turbine T013 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,12.5,'v1.13',@ActorOid,'2026-07-23T12:00:00'),
    ('80000000-0000-0000-0000-000000000014','EQUIP_RTI_T014','Turbine T014 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,14.0,'v1.14',@ActorOid,'2026-07-24T12:00:00'),
    ('80000000-0000-0000-0000-000000000015','EQUIP_RTI_T015','Turbine T015 digital twin','GLB','https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb',NULL,8.0,'v1.15',@ActorOid,'2026-07-10T12:00:00')
) AS source(id,equipmentId,modelName,format,modelUrl,thumbnailUrl,fileSizeMb,version,updatedByOid,updatedAt)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET modelName=source.modelName,format=source.format,modelUrl=source.modelUrl,fileSizeMb=source.fileSizeMb,version=source.version,updatedAt=source.updatedAt
WHEN NOT MATCHED THEN INSERT (id,equipmentId,modelName,format,modelUrl,thumbnailUrl,fileSizeMb,version,updatedByOid,updatedAt)
VALUES (source.id,source.equipmentId,source.modelName,source.format,source.modelUrl,source.thumbnailUrl,source.fileSizeMb,source.version,source.updatedByOid,source.updatedAt);

COMMIT TRANSACTION;

SELECT 'WorkOrder' entity, COUNT(*) row_count FROM dbo.WorkOrders
UNION ALL SELECT 'MaintenanceNotification', COUNT(*) FROM dbo.MaintenanceNotifications
UNION ALL SELECT 'Inspection', COUNT(*) FROM dbo.Inspections
UNION ALL SELECT 'SparePart', COUNT(*) FROM dbo.SpareParts
UNION ALL SELECT 'Asset3DModel', COUNT(*) FROM dbo.Asset3DModels;
