import { initEmbeddedAuth, ensureSignedInWithFabric } from '@microsoft/rayfin-auth-provider-fabric'
import { RayfinClient } from '@microsoft/rayfin-client'
import {
  seedWorkOrders, seedNotifications, seedInspections, seedSpareParts, seedAsset3DModels,
} from './seedData'

export type WorkOrderRecord = {
  id: string
  workOrderNumber: string
  equipmentId: string
  instrumentId?: string
  opcuaNodeId?: string
  title: string
  description?: string
  priority: string
  status: string
  assignedToOid?: string
  createdByOid: string
  createdAt: Date
  dueAt?: Date
  completedAt?: Date
}

type HydroSchema = {
  WorkOrder: WorkOrderRecord
  Inspection: InspectionRecord
  SparePart: SparePartRecord
  Asset3DModel: Asset3DModelRecord
  MaintenanceNotification: MaintenanceNotificationRecord
}

export type InspectionRecord = {
  id: string
  equipmentId: string
  opcuaNodeId?: string
  inspectionType: string
  result: string
  findings?: string
  inspectorOid: string
  inspectedAt: Date
  nextDueAt?: Date
}

export type SparePartRecord = {
  id: string
  partNumber: string
  name: string
  category: string
  equipmentType: string
  quantityOnHand: number
  reorderLevel: number
  storageLocation: string
  unitCostUsd?: number
  lastRestockedAt?: Date
}

export type Asset3DModelRecord = {
  id: string
  equipmentId: string
  modelName: string
  format: string
  modelUrl: string
  thumbnailUrl?: string
  fileSizeMb?: number
  version?: string
  updatedByOid: string
  updatedAt: Date
}

export type MaintenanceNotificationRecord = {
  id: string
  equipmentId: string
  opcuaNodeId?: string
  summary: string
  severity: string
  status: string
  reportedByOid: string
  reportedAt: Date
}

export type AppUser = { id: string; email: string; name: string }

const apiUrl = import.meta.env.VITE_RAYFIN_API_URL as string | undefined
const publishableKey = import.meta.env.VITE_RAYFIN_PUBLISHABLE_KEY as string | undefined
const workspaceId = (import.meta.env.VITE_FABRIC_WORKSPACE_ID ?? import.meta.env.VITE_RAYFIN_WORKSPACE_ID) as string | undefined
const projectId = (import.meta.env.VITE_FABRIC_ITEM_ID ?? import.meta.env.VITE_RAYFIN_ITEM_ID) as string | undefined
const fabricPortalUrl = (import.meta.env.VITE_FABRIC_PORTAL_URL ?? import.meta.env.VITE_RAYFIN_PORTAL_URL ?? 'https://app.fabric.microsoft.com') as string

const configured = Boolean(apiUrl && publishableKey && workspaceId && projectId)
const client = configured ? new RayfinClient<HydroSchema>({
  baseUrl: apiUrl!.endsWith('/') ? apiUrl! : `${apiUrl}/`,
  publishableKey: publishableKey!,
  useProxy: false,
  authStorage: true,
}) : undefined

function options() {
  if (!workspaceId || !projectId) throw new Error('Fabric app identity is not configured.')
  return { workspaceId, projectId, fabricPortalUrl, returnOrigin: location.origin }
}

function currentUser(): AppUser | null {
  const session = client?.auth.getSession()
  if (!session?.isAuthenticated || !session.user) return null
  return { id: session.user.id, email: session.user.email, name: session.user.email.split('@')[0] }
}

export function isRayfinConfigured() { return configured }

export async function initializeRayfin(): Promise<AppUser | null> {
  if (!client) return null
  const existing = currentUser()
  if (existing) return existing
  const session = await initEmbeddedAuth(client.auth, options())
  if (!session?.isAuthenticated || !session.user) return null
  return currentUser()
}

export async function signInToRayfin(): Promise<AppUser> {
  if (!client) throw new Error('Rayfin backend is not configured.')
  const session = await ensureSignedInWithFabric(client.auth, options())
  if (!session.isAuthenticated || !session.user) throw new Error('Fabric sign-in did not establish a session.')
  return currentUser()!
}

export async function listWorkOrders(): Promise<WorkOrderRecord[]> {
  if (!client) return []
  return await client.data.WorkOrder.select([
    'id', 'workOrderNumber', 'equipmentId', 'instrumentId', 'opcuaNodeId', 'title',
    'description', 'priority', 'status', 'assignedToOid', 'createdByOid', 'createdAt',
    'dueAt', 'completedAt',
  ]).orderBy({ createdAt: 'desc' }).execute() as WorkOrderRecord[]
}

export async function createWorkOrder(user: AppUser, equipmentId: string, instrumentId?: string, opcuaNodeId?: string): Promise<WorkOrderRecord> {
  if (!client) throw new Error('Rayfin backend is not configured.')
  return await client.data.WorkOrder.create({
    workOrderNumber: `WO-${Date.now().toString().slice(-6)}`,
    equipmentId,
    instrumentId,
    opcuaNodeId,
    title: `Inspect ${equipmentId}`,
    description: 'Operator-created inspection from Hydro Operations.',
    priority: 'High',
    status: 'Draft',
    createdByOid: user.id,
    createdAt: new Date(),
  }) as WorkOrderRecord
}

export async function listInspections(): Promise<InspectionRecord[]> {
  if (!client) return []
  return await client.data.Inspection.select([
    'id', 'equipmentId', 'opcuaNodeId', 'inspectionType', 'result', 'findings',
    'inspectorOid', 'inspectedAt', 'nextDueAt',
  ]).orderBy({ inspectedAt: 'desc' }).execute() as InspectionRecord[]
}

export async function listSpareParts(): Promise<SparePartRecord[]> {
  if (!client) return []
  return await client.data.SparePart.select([
    'id', 'partNumber', 'name', 'category', 'equipmentType', 'quantityOnHand',
    'reorderLevel', 'storageLocation', 'unitCostUsd', 'lastRestockedAt',
  ]).orderBy({ partNumber: 'asc' }).execute() as SparePartRecord[]
}

export async function listAsset3DModels(): Promise<Asset3DModelRecord[]> {
  if (!client) return []
  return await client.data.Asset3DModel.select([
    'id', 'equipmentId', 'modelName', 'format', 'modelUrl', 'thumbnailUrl',
    'fileSizeMb', 'version', 'updatedByOid', 'updatedAt',
  ]).orderBy({ updatedAt: 'desc' }).execute() as Asset3DModelRecord[]
}

export async function listMaintenanceNotifications(): Promise<MaintenanceNotificationRecord[]> {
  if (!client) return []
  return await client.data.MaintenanceNotification.select([
    'id', 'equipmentId', 'opcuaNodeId', 'summary', 'severity', 'status',
    'reportedByOid', 'reportedAt',
  ]).orderBy({ reportedAt: 'desc' }).execute() as MaintenanceNotificationRecord[]
}

export type SeedSummary = { entity: string; created: number; skipped: boolean }

// Idempotent client-side self-seeder. Rayfin exposes no direct SQL host, so the
// documented pattern is to seed demo data through the authenticated data client.
// Each entity is seeded only when its table is empty, so this is safe to re-run.
export async function seedOperationalDataIfEmpty(user: AppUser): Promise<SeedSummary[]> {
  if (!client) return []
  const oid = user.id
  const summary: SeedSummary[] = []

  const existingWo = await client.data.WorkOrder.select(['id', 'workOrderNumber']).execute() as { id: string; workOrderNumber: string }[]
  const knownWo = new Set(existingWo.map(w => w.workOrderNumber))
  const missingWo = seedWorkOrders.filter(w => !knownWo.has(w.workOrderNumber))
  if (missingWo.length) {
    for (const w of missingWo) {
      await client.data.WorkOrder.create({
        workOrderNumber: w.workOrderNumber, equipmentId: w.equipmentId, instrumentId: w.instrumentId,
        opcuaNodeId: w.opcuaNodeId, title: w.title, description: w.description, priority: w.priority,
        status: w.status, assignedToOid: w.assigned ? oid : undefined, createdByOid: oid,
        createdAt: new Date(w.createdAt), dueAt: w.dueAt ? new Date(w.dueAt) : undefined,
      })
    }
    summary.push({ entity: 'WorkOrder', created: missingWo.length, skipped: false })
  } else {
    summary.push({ entity: 'WorkOrder', created: 0, skipped: true })
  }

  const existingNotif = await client.data.MaintenanceNotification.select(['id', 'equipmentId', 'summary']).execute() as { id: string; equipmentId: string; summary: string }[]
  const knownNotif = new Set(existingNotif.map(n => `${n.equipmentId}|${n.summary}`))
  const missingNotif = seedNotifications.filter(n => !knownNotif.has(`${n.equipmentId}|${n.summary}`))
  if (missingNotif.length) {
    for (const n of missingNotif) {
      await client.data.MaintenanceNotification.create({
        equipmentId: n.equipmentId, opcuaNodeId: n.opcuaNodeId, summary: n.summary,
        severity: n.severity, status: n.status, reportedByOid: oid, reportedAt: new Date(n.reportedAt),
      })
    }
    summary.push({ entity: 'MaintenanceNotification', created: missingNotif.length, skipped: false })
  } else {
    summary.push({ entity: 'MaintenanceNotification', created: 0, skipped: true })
  }

  const existingInsp = await client.data.Inspection.select(['id']).execute() as { id: string }[]
  if (existingInsp.length === 0) {
    for (const ins of seedInspections) {
      await client.data.Inspection.create({
        equipmentId: ins.equipmentId, opcuaNodeId: ins.opcuaNodeId, inspectionType: ins.inspectionType,
        result: ins.result, findings: ins.findings, inspectorOid: oid,
        inspectedAt: new Date(ins.inspectedAt), nextDueAt: ins.nextDueAt ? new Date(ins.nextDueAt) : undefined,
      })
    }
    summary.push({ entity: 'Inspection', created: seedInspections.length, skipped: false })
  } else {
    summary.push({ entity: 'Inspection', created: 0, skipped: true })
  }

  const existingParts = await client.data.SparePart.select(['id']).execute() as { id: string }[]
  if (existingParts.length === 0) {
    for (const p of seedSpareParts) {
      await client.data.SparePart.create({
        partNumber: p.partNumber, name: p.name, category: p.category, equipmentType: p.equipmentType,
        quantityOnHand: p.quantityOnHand, reorderLevel: p.reorderLevel, storageLocation: p.storageLocation,
        unitCostUsd: p.unitCostUsd, lastRestockedAt: p.lastRestockedAt ? new Date(p.lastRestockedAt) : undefined,
      })
    }
    summary.push({ entity: 'SparePart', created: seedSpareParts.length, skipped: false })
  } else {
    summary.push({ entity: 'SparePart', created: 0, skipped: true })
  }

  const existingModels = await client.data.Asset3DModel.select(['id']).execute() as { id: string }[]
  if (existingModels.length === 0) {
    for (const m of seedAsset3DModels) {
      await client.data.Asset3DModel.create({
        equipmentId: m.equipmentId, modelName: m.modelName, format: m.format, modelUrl: m.modelUrl,
        thumbnailUrl: m.thumbnailUrl, fileSizeMb: m.fileSizeMb, version: m.version,
        updatedByOid: oid, updatedAt: new Date(m.updatedAt),
      })
    }
    summary.push({ entity: 'Asset3DModel', created: seedAsset3DModels.length, skipped: false })
  } else {
    summary.push({ entity: 'Asset3DModel', created: 0, skipped: true })
  }

  return summary
}