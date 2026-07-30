import { initEmbeddedAuth, ensureSignedInWithFabric } from '@microsoft/rayfin-auth-provider-fabric'
import { RayfinClient } from '@microsoft/rayfin-client'

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

type HydroSchema = { WorkOrder: WorkOrderRecord }

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