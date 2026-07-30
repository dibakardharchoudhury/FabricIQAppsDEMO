SET NOCOUNT ON;

IF (SELECT COUNT(*) FROM dbo.WorkOrders WHERE workOrderNumber IN ('WO-2841','WO-2836','WO-2829')) <> 3
    THROW 51000, 'Expected the three baseline work orders.', 1;
IF EXISTS (SELECT equipmentId FROM dbo.WorkOrders GROUP BY equipmentId HAVING COUNT(*) > 1 AND equipmentId IS NULL)
    THROW 51000, 'Seed contains invalid equipment references.', 1;
IF (SELECT COUNT(*) FROM dbo.Asset3DModels) < 15
    THROW 51000, 'Expected a 3D model per turbine (15).', 1;
IF (SELECT COUNT(*) FROM dbo.SpareParts) < 12
    THROW 51000, 'Expected the spare-part inventory (12 SKUs).', 1;
IF (SELECT COUNT(*) FROM dbo.Inspections) < 30
    THROW 51000, 'Expected two inspections per turbine (30).', 1;

SELECT 'WorkOrder' entity, COUNT(*) row_count FROM dbo.WorkOrders
UNION ALL SELECT 'MaintenanceNotification', COUNT(*) FROM dbo.MaintenanceNotifications
UNION ALL SELECT 'Inspection', COUNT(*) FROM dbo.Inspections
UNION ALL SELECT 'SparePart', COUNT(*) FROM dbo.SpareParts
UNION ALL SELECT 'Asset3DModel', COUNT(*) FROM dbo.Asset3DModels
ORDER BY entity;