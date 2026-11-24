import { RayfinClient } from '@microsoft/rayfin-client'
import { ensureSignedInWithFabric } from '@microsoft/rayfin-auth-provider-fabric'
import { canonicalJson, fingerprint, type Contract } from './contract'

export type Draft = { id: string; name: string; contentHash: string; contractJson: string; createdBy: string; createdAt: Date }
const baseUrl = import.meta.env.VITE_RAYFIN_API_URL as string | undefined
const publishableKey = import.meta.env.VITE_RAYFIN_PUBLISHABLE_KEY as string | undefined
const workspaceId = (import.meta.env.VITE_FABRIC_WORKSPACE_ID ?? import.meta.env.VITE_RAYFIN_WORKSPACE_ID) as string | undefined
const projectId = (import.meta.env.VITE_FABRIC_ITEM_ID ?? import.meta.env.VITE_RAYFIN_ITEM_ID) as string | undefined
const client = baseUrl && publishableKey ? new RayfinClient<{ MasterDataDraft: Draft }>({
  baseUrl: baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`, publishableKey, authStorage: true,
}) : undefined

export async function signIn() {
  if (!client || !workspaceId || !projectId) throw new Error('Fabric hosting is not configured. Draft downloads work offline; SQL history requires the configured app and applied MasterDataDraft schema.')
  const session = await ensureSignedInWithFabric(client.auth, {
    workspaceId, projectId, fabricPortalUrl: 'https://app.fabric.microsoft.com', returnOrigin: location.origin,
  })
  if (!session?.isAuthenticated || !session.user) throw new Error('Fabric sign-in did not establish a session.')
  return session.user.id
}
export async function saveDraft(contract: Contract): Promise<Draft> {
  if (!client) throw new Error('Fabric SQL draft history is not configured.')
  const user = client.auth.getSession()?.user
  if (!user) throw new Error('Sign in before saving a draft.')
  const json = canonicalJson(contract)
  if (json.length > 1000000) throw new Error('Draft exceeds the one-million-character metadata limit.')
  const draft: Draft = {
    id: crypto.randomUUID(), name: contract.name, contractJson: json, contentHash: await fingerprint(contract),
    createdBy: user.id, createdAt: new Date(),
  }
  await client.data.MasterDataDraft.create(draft)
  const readback = await client.data.MasterDataDraft.findById(draft.id)
  if (!readback || readback.contentHash !== draft.contentHash || readback.contractJson !== json) {
    throw new Error('SQL draft readback did not match. Delivery is uncertain; inspect history before retrying.')
  }
  return draft
}
export async function listDrafts(cursor?: string) {
  if (!client || !client.auth.getSession()?.isAuthenticated) throw new Error('Sign in before reading SQL history.')
  return client.data.MasterDataDraft.select(['id', 'name', 'contentHash', 'contractJson', 'createdBy', 'createdAt'])
    .orderBy({ createdAt: 'desc', id: 'desc' }).first(20).after(cursor).executePaginated()
}
