"""Generate HydroOperationsApp/sql/seed-operational-data.sql for 3 facilities.

Idempotent MERGE seed for app-owned operational tables. All equipmentId / opcua keys
resolve to STID Lakehouse rows produced by the 3-facility STID seed. Delete after use.
"""
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "HydroOperationsApp" / "sql" / "seed-operational-data.sql"

# Turbine tag -> facility index
TURBINES = [(f"T{n:03d}", (1 if n <= 5 else 2 if n <= 10 else 3)) for n in range(1, 16)]
MANUF = {1: "Andritz", 2: "Voith", 3: "GE Vernova", 4: "Toshiba", 0: "Hitachi Energy"}


def eq(tag):
    return f"EQUIP_RTI_{tag}"


def node(tag, signal):
    return f"ns=2;s={tag}.{signal}"


def guid(prefix, n):
    return f"{prefix}-0000-0000-0000-{n:012d}"


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


lines = []
w = lines.append

w("-- Idempotent sample operational data for the Hydro Operations app (3 facilities).")
w("-- App-owned mutable records only. Every equipmentId / opcua_node_id resolves to an")
w("-- existing STID Lakehouse row seeded by RTI_001_create_lakehouse_SelfContained.")
w("-- Regenerate with: python _gen_seed.py  (repo root).")
w("SET NOCOUNT ON;")
w("SET XACT_ABORT ON;")
w("BEGIN TRANSACTION;")
w("")
w("DECLARE @ActorOid nvarchar(200) = '$(ActorOid)';")
w("IF @ActorOid = '$' + '(ActorOid)' OR NULLIF(@ActorOid, '') IS NULL")
w("    SET @ActorOid = '00000000-0000-0000-0000-000000000000';")
w("")

# ---------------- Work Orders ----------------
work_orders = [
    ("WO-2841", "T001", "vibration_a", "Inspect guide bearing vibration", "Inspect bearing, alignment, and lubrication after elevated vibration trend.", "Critical", "In progress", "@ActorOid", "2026-07-28T08:00:00", "2026-08-01T16:00:00"),
    ("WO-2836", "T002", "inlet_pressure", "Review inlet pressure variance", "Validate transducer and compare against local gauge.", "High", "Scheduled", "@ActorOid", "2026-07-27T10:00:00", "2026-08-03T12:00:00"),
    ("WO-2829", "T003", "turbine_temp", "Inspect turbine cooling circuit", "Check strainers, flow, and temperature sensor calibration.", "Medium", "Ready", "NULL", "2026-07-26T09:30:00", "2026-08-05T12:00:00"),
    ("WO-2848", "T006", "vibration_d", "Fjord unit driven-end vibration check", "Trend review and balancing assessment on driven-end bearing.", "High", "Approved", "@ActorOid", "2026-07-29T07:00:00", "2026-08-04T16:00:00"),
    ("WO-2851", "T008", "power_output", "Investigate power output dip", "Correlate output with head and guide-vane position.", "Medium", "Planned", "NULL", "2026-07-29T13:20:00", "2026-08-06T12:00:00"),
    ("WO-2853", "T010", "turbine_speed", "Overspeed trip test", "Scheduled governor overspeed protection test.", "Low", "Scheduled", "@ActorOid", "2026-07-30T09:00:00", "2026-08-08T12:00:00"),
    ("WO-2857", "T011", "turbine_temp", "Highland unit cooling inspection", "Inspect heat exchanger fouling after temperature rise.", "High", "In progress", "@ActorOid", "2026-07-30T11:00:00", "2026-08-02T18:00:00"),
    ("WO-2860", "T013", "vibration_a", "Highland unit bearing endoscopy", "Borescope guide bearing following vibration advisory.", "Critical", "Ready", "NULL", "2026-07-30T14:10:00", "2026-08-03T12:00:00"),
    ("WO-2862", "T015", "inlet_pressure", "Inlet strainer differential check", "Verify strainer differential pressure and clean if required.", "Medium", "Approved", "@ActorOid", "2026-07-31T08:30:00", "2026-08-09T12:00:00"),
    ("WO-2820", "T004", "turbine_temp", "Annual thermographic survey", "Completed annual thermographic survey; no anomalies.", "Low", "Completed", "@ActorOid", "2026-07-15T08:00:00", "2026-07-20T12:00:00"),
    ("WO-2812", "T007", "vibration_a", "Bearing lubrication refresh", "Completed lubrication service on guide bearing.", "Medium", "Completed", "@ActorOid", "2026-07-10T08:00:00", "2026-07-14T12:00:00"),
    ("WO-2867", "T012", "power_output", "Efficiency test after overhaul", "Post-overhaul efficiency verification test.", "High", "Draft", "NULL", "2026-07-31T15:00:00", "2026-08-12T12:00:00"),
]
w("MERGE dbo.WorkOrders AS target")
w("USING (VALUES")
rows = []
for i, (wo, tag, sig, title, desc, prio, status, assigned, created, due) in enumerate(work_orders, start=1):
    rows.append(
        f"    ({q(guid('10000000', i))},{q(wo)},{q(eq(tag))},{q('INST_' + tag + '_' + sig.upper())},"
        f"{q(node(tag, sig))},{q(title)},{q(desc)},{q(prio)},{q(status)},{assigned},@ActorOid,{q(created)},{q(due)})"
    )
w(",\n".join(rows))
w(") AS source(id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)")
w("ON target.workOrderNumber = source.workOrderNumber")
w("WHEN MATCHED THEN UPDATE SET title=source.title, description=source.description, priority=source.priority, status=source.status, assignedToOid=source.assignedToOid, dueAt=source.dueAt")
w("WHEN NOT MATCHED THEN INSERT (id,workOrderNumber,equipmentId,instrumentId,opcuaNodeId,title,description,priority,status,assignedToOid,createdByOid,createdAt,dueAt)")
w("VALUES (source.id,source.workOrderNumber,source.equipmentId,source.instrumentId,source.opcuaNodeId,source.title,source.description,source.priority,source.status,source.assignedToOid,source.createdByOid,source.createdAt,source.dueAt);")
w("")

# ---------------- Maintenance Notifications ----------------
notifications = [
    ("T001", "vibration_a", "Guide bearing vibration exceeded advisory threshold", "Warning", "Open", "2026-07-30T07:42:00"),
    ("T002", "inlet_pressure", "Inlet pressure variance requires review", "Advisory", "Open", "2026-07-30T06:15:00"),
    ("T006", "vibration_d", "Driven-end vibration trending upward", "Warning", "Open", "2026-07-29T18:05:00"),
    ("T008", "power_output", "Power output below expected for head", "Advisory", "Acknowledged", "2026-07-29T12:40:00"),
    ("T011", "turbine_temp", "Bearing temperature above normal band", "Warning", "Open", "2026-07-30T10:20:00"),
    ("T013", "vibration_a", "Vibration advisory on guide bearing", "Critical", "Open", "2026-07-30T13:55:00"),
]
w("MERGE dbo.MaintenanceNotifications AS target")
w("USING (VALUES")
rows = []
for i, (tag, sig, summary, sev, status, reported) in enumerate(notifications, start=1):
    rows.append(f"    ({q(guid('20000000', i))},{q(eq(tag))},{q(node(tag, sig))},{q(summary)},{q(sev)},{q(status)},@ActorOid,{q(reported)})")
w(",\n".join(rows))
w(") AS source(id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)")
w("ON target.id = source.id")
w("WHEN MATCHED THEN UPDATE SET summary=source.summary,severity=source.severity,status=source.status")
w("WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,summary,severity,status,reportedByOid,reportedAt)")
w("VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.summary,source.severity,source.status,source.reportedByOid,source.reportedAt);")
w("")

# ---------------- Inspections (2 per turbine) ----------------
insp_variants = [
    ("VISUAL", None, "PASS", "Casing, fasteners, and seals visually normal."),
    ("THERMOGRAPHIC", "turbine_temp", "PASS", "No hot spots detected on bearings or windings."),
    ("VIBRATION", "vibration_a", "ATTENTION", "Guide bearing vibration slightly above baseline; monitor."),
    ("LUBRICATION", None, "PASS", "Oil level and quality within specification."),
]
w("MERGE dbo.Inspections AS target")
w("USING (VALUES")
rows = []
counter = 1
for ti, (tag, fac) in enumerate(TURBINES):
    # First inspection: always VISUAL PASS (recent). Second: rotate the other variants.
    first = insp_variants[0]
    second = insp_variants[1 + (ti % 3)]
    for j, (itype, sig, result, findings) in enumerate((first, second)):
        # Make a couple of assets show FAIL/ATTENTION for realism.
        r = result
        if tag in ("T013",) and itype == "VIBRATION":
            r, findings = "FAIL", "Guide bearing vibration exceeded alarm limit; schedule intervention."
        day = 20 + (ti % 8)
        inspected = f"2026-07-{day:02d}T09:00:00"
        nxt = "2026-10-15T09:00:00"
        onode = q(node(tag, sig)) if sig else "NULL"
        rows.append(f"    ({q(guid('60000000', counter))},{q(eq(tag))},{onode},{q(itype)},{q(r)},{q(findings)},@ActorOid,{q(inspected)},{q(nxt)})")
        counter += 1
w(",\n".join(rows))
w(") AS source(id,equipmentId,opcuaNodeId,inspectionType,result,findings,inspectorOid,inspectedAt,nextDueAt)")
w("ON target.id = source.id")
w("WHEN MATCHED THEN UPDATE SET inspectionType=source.inspectionType,result=source.result,findings=source.findings,inspectedAt=source.inspectedAt,nextDueAt=source.nextDueAt")
w("WHEN NOT MATCHED THEN INSERT (id,equipmentId,opcuaNodeId,inspectionType,result,findings,inspectorOid,inspectedAt,nextDueAt)")
w("VALUES (source.id,source.equipmentId,source.opcuaNodeId,source.inspectionType,source.result,source.findings,source.inspectorOid,source.inspectedAt,source.nextDueAt);")
w("")

# ---------------- Spare Parts ----------------
spare_parts = [
    ("SP-BRG-1001", "Guide bearing pad set", "BEARING", "TURB", 6, 4, "Warehouse A-01", 4200.00, "2026-06-10"),
    ("SP-BRG-1002", "Thrust bearing segment", "BEARING", "TURB", 2, 3, "Warehouse A-01", 5100.00, "2026-05-22"),
    ("SP-SEAL-2001", "Main shaft seal kit", "SEAL", "TURB", 9, 5, "Warehouse A-02", 880.00, "2026-06-28"),
    ("SP-SEAL-2002", "Guide vane seal ring", "SEAL", "TURB", 3, 4, "Warehouse A-02", 640.00, "2026-04-30"),
    ("SP-SEN-3001", "Vibration probe (eddy current)", "SENSOR", "TURB", 12, 6, "Store B-11", 1350.00, "2026-07-05"),
    ("SP-SEN-3002", "RTD temperature sensor", "SENSOR", "TURB", 20, 8, "Store B-11", 210.00, "2026-07-12"),
    ("SP-SEN-3003", "Pressure transducer", "SENSOR", "TURB", 7, 6, "Store B-12", 490.00, "2026-06-18"),
    ("SP-VAL-4001", "Guide vane servo valve", "VALVE", "TURB", 2, 2, "Warehouse A-03", 7300.00, "2026-03-15"),
    ("SP-VAL-4002", "Cooling water control valve", "VALVE", "TURB", 5, 3, "Warehouse A-03", 1120.00, "2026-06-02"),
    ("SP-ELE-5001", "Excitation control card", "ELECTRICAL", "TURB", 4, 3, "Store B-20", 2650.00, "2026-05-09"),
    ("SP-ELE-5002", "Governor PLC module", "ELECTRICAL", "TURB", 1, 2, "Store B-20", 3900.00, "2026-02-27"),
    ("SP-ELE-5003", "Field cabling loom", "ELECTRICAL", "TURB", 14, 6, "Store B-21", 180.00, "2026-07-20"),
]
w("MERGE dbo.SpareParts AS target")
w("USING (VALUES")
rows = []
for i, (pn, name, cat, etype, qty, reorder, loc, cost, restock) in enumerate(spare_parts, start=1):
    rows.append(f"    ({q(guid('70000000', i))},{q(pn)},{q(name)},{q(cat)},{q(etype)},{qty},{reorder},{q(loc)},{cost},{q(restock)})")
w(",\n".join(rows))
w(") AS source(id,partNumber,name,category,equipmentType,quantityOnHand,reorderLevel,storageLocation,unitCostUsd,lastRestockedAt)")
w("ON target.partNumber = source.partNumber")
w("WHEN MATCHED THEN UPDATE SET name=source.name,category=source.category,quantityOnHand=source.quantityOnHand,reorderLevel=source.reorderLevel,storageLocation=source.storageLocation,unitCostUsd=source.unitCostUsd,lastRestockedAt=source.lastRestockedAt")
w("WHEN NOT MATCHED THEN INSERT (id,partNumber,name,category,equipmentType,quantityOnHand,reorderLevel,storageLocation,unitCostUsd,lastRestockedAt)")
w("VALUES (source.id,source.partNumber,source.name,source.category,source.equipmentType,source.quantityOnHand,source.reorderLevel,source.storageLocation,source.unitCostUsd,source.lastRestockedAt);")
w("")

# ---------------- Asset 3D Models (1 per turbine) ----------------
SAMPLE_GLB = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/DamagedHelmet/glTF-Binary/DamagedHelmet.glb"
w("MERGE dbo.Asset3DModels AS target")
w("USING (VALUES")
rows = []
for i, (tag, fac) in enumerate(TURBINES, start=1):
    name = f"Turbine {tag} digital twin"
    size = round(8.0 + (i % 5) * 1.5, 1)
    ver = f"v1.{i}"
    updated = f"2026-07-{10 + (i % 15):02d}T12:00:00"
    rows.append(f"    ({q(guid('80000000', i))},{q(eq(tag))},{q(name)},{q('GLB')},{q(SAMPLE_GLB)},NULL,{size},{q(ver)},@ActorOid,{q(updated)})")
w(",\n".join(rows))
w(") AS source(id,equipmentId,modelName,format,modelUrl,thumbnailUrl,fileSizeMb,version,updatedByOid,updatedAt)")
w("ON target.id = source.id")
w("WHEN MATCHED THEN UPDATE SET modelName=source.modelName,format=source.format,modelUrl=source.modelUrl,fileSizeMb=source.fileSizeMb,version=source.version,updatedAt=source.updatedAt")
w("WHEN NOT MATCHED THEN INSERT (id,equipmentId,modelName,format,modelUrl,thumbnailUrl,fileSizeMb,version,updatedByOid,updatedAt)")
w("VALUES (source.id,source.equipmentId,source.modelName,source.format,source.modelUrl,source.thumbnailUrl,source.fileSizeMb,source.version,source.updatedByOid,source.updatedAt);")
w("")

w("COMMIT TRANSACTION;")
w("")
w("SELECT 'WorkOrder' entity, COUNT(*) row_count FROM dbo.WorkOrders")
w("UNION ALL SELECT 'MaintenanceNotification', COUNT(*) FROM dbo.MaintenanceNotifications")
w("UNION ALL SELECT 'Inspection', COUNT(*) FROM dbo.Inspections")
w("UNION ALL SELECT 'SparePart', COUNT(*) FROM dbo.SpareParts")
w("UNION ALL SELECT 'Asset3DModel', COUNT(*) FROM dbo.Asset3DModels;")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Wrote", OUT)
print("WorkOrders:", len(work_orders), "Notifications:", len(notifications), "Inspections:", counter - 1,
      "SpareParts:", len(spare_parts), "Models:", len(TURBINES))


# =====================================================================
# Also emit a TypeScript seed module consumed by the app's client-side
# self-seeder (Rayfin has no reachable SQL host; the documented Rayfin
# pattern is to seed through the authenticated data client). Same data
# as the SQL file above, minus server-assigned ids / actor oid.
# =====================================================================
import json

TS_OUT = ROOT / "HydroOperationsApp" / "src" / "services" / "seedData.ts"


def js(v):
    if v is None:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v))


def obj(pairs):
    inner = ", ".join(f"{k}: {js(v)}" for k, v in pairs if v is not None or k in OPTIONAL_KEEP)
    return "  { " + inner + " },"


# Keys we always emit even when undefined would otherwise be dropped (none needed today)
OPTIONAL_KEEP: set = set()

t = []
tw = t.append
tw("// AUTO-GENERATED by _gen_seed.py — do not edit by hand.")
tw("// Demo operational seed data (3 facilities) for the client-side self-seeder.")
tw("// Dates are ISO strings; the seeder converts them to Date and stamps the actor oid.")
tw("")

# Work orders
tw("export interface SeedWorkOrder {")
tw("  workOrderNumber: string; equipmentId: string; instrumentId?: string; opcuaNodeId?: string")
tw("  title: string; description: string; priority: string; status: string")
tw("  assigned: boolean; createdAt: string; dueAt?: string")
tw("}")
tw("export const seedWorkOrders: SeedWorkOrder[] = [")
for (wo, tag, sig, title, desc, prio, status, assigned, created, due) in work_orders:
    tw(obj([
        ("workOrderNumber", wo), ("equipmentId", eq(tag)),
        ("instrumentId", "INST_" + tag + "_" + sig.upper()), ("opcuaNodeId", node(tag, sig)),
        ("title", title), ("description", desc), ("priority", prio), ("status", status),
        ("assigned", assigned == "@ActorOid"), ("createdAt", created), ("dueAt", due),
    ]))
tw("]")
tw("")

# Maintenance notifications
tw("export interface SeedNotification {")
tw("  equipmentId: string; opcuaNodeId?: string; summary: string; severity: string; status: string; reportedAt: string")
tw("}")
tw("export const seedNotifications: SeedNotification[] = [")
for (tag, sig, summary, sev, status, reported) in notifications:
    tw(obj([
        ("equipmentId", eq(tag)), ("opcuaNodeId", node(tag, sig)),
        ("summary", summary), ("severity", sev), ("status", status), ("reportedAt", reported),
    ]))
tw("]")
tw("")

# Inspections (reconstruct the same records as the SQL block)
insp_records = []
for ti, (tag, fac) in enumerate(TURBINES):
    first = insp_variants[0]
    second = insp_variants[1 + (ti % 3)]
    for (itype, sig, result, findings) in (first, second):
        r, fnd = result, findings
        if tag == "T013" and itype == "VIBRATION":
            r, fnd = "FAIL", "Guide bearing vibration exceeded alarm limit; schedule intervention."
        day = 20 + (ti % 8)
        insp_records.append((tag, sig, itype, r, fnd, f"2026-07-{day:02d}T09:00:00", "2026-10-15T09:00:00"))
tw("export interface SeedInspection {")
tw("  equipmentId: string; opcuaNodeId?: string; inspectionType: string; result: string")
tw("  findings?: string; inspectedAt: string; nextDueAt?: string")
tw("}")
tw("export const seedInspections: SeedInspection[] = [")
for (tag, sig, itype, r, fnd, inspected, nxt) in insp_records:
    tw(obj([
        ("equipmentId", eq(tag)), ("opcuaNodeId", node(tag, sig) if sig else None),
        ("inspectionType", itype), ("result", r), ("findings", fnd),
        ("inspectedAt", inspected), ("nextDueAt", nxt),
    ]))
tw("]")
tw("")

# Spare parts
tw("export interface SeedSparePart {")
tw("  partNumber: string; name: string; category: string; equipmentType: string")
tw("  quantityOnHand: number; reorderLevel: number; storageLocation: string")
tw("  unitCostUsd?: number; lastRestockedAt?: string")
tw("}")
tw("export const seedSpareParts: SeedSparePart[] = [")
for (pn, name, cat, etype, qty, reorder, loc, cost, restock) in spare_parts:
    tw(obj([
        ("partNumber", pn), ("name", name), ("category", cat), ("equipmentType", etype),
        ("quantityOnHand", qty), ("reorderLevel", reorder), ("storageLocation", loc),
        ("unitCostUsd", cost), ("lastRestockedAt", restock),
    ]))
tw("]")
tw("")

# Asset 3D models
tw("export interface SeedAsset3DModel {")
tw("  equipmentId: string; modelName: string; format: string; modelUrl: string")
tw("  thumbnailUrl?: string; fileSizeMb?: number; version?: string; updatedAt: string")
tw("}")
tw("export const seedAsset3DModels: SeedAsset3DModel[] = [")
for i, (tag, fac) in enumerate(TURBINES, start=1):
    tw(obj([
        ("equipmentId", eq(tag)), ("modelName", f"Turbine {tag} digital twin"),
        ("format", "GLB"), ("modelUrl", SAMPLE_GLB), ("thumbnailUrl", None),
        ("fileSizeMb", round(8.0 + (i % 5) * 1.5, 1)), ("version", f"v1.{i}"),
        ("updatedAt", f"2026-07-{10 + (i % 15):02d}T12:00:00"),
    ]))
tw("]")
tw("")

TS_OUT.write_text("\n".join(t) + "\n", encoding="utf-8")
print("Wrote", TS_OUT)
