SET NOCOUNT ON;

IF (SELECT COUNT(*) FROM dbo.WorkOrders WHERE workOrderNumber IN ('WO-2841','WO-2836','WO-2829')) <> 3
    THROW 51000, 'Expected three seeded work orders.', 1;
IF EXISTS (SELECT equipmentId FROM dbo.WorkOrders GROUP BY equipmentId HAVING COUNT(*) > 1 AND equipmentId IS NULL)
    THROW 51000, 'Seed contains invalid equipment references.', 1;

SELECT workOrderNumber,equipmentId,instrumentId,opcuaNodeId,priority,status
FROM dbo.WorkOrders
WHERE workOrderNumber IN ('WO-2841','WO-2836','WO-2829')
ORDER BY workOrderNumber;