import { PublicClientApplication } from '@azure/msal-browser'
import { parseOntologyContract, type OntologyContract, type OntologyDefinition } from '../services/ontologyContract'
import type { Contract } from './contract'
import { definitionFingerprint } from './ontologyValidation'
export { ontologyIssues } from './ontologyValidation'

const clientId = import.meta.env.VITE_RAYFIN_AAD_CLIENT_ID as string | undefined
const tenant = (import.meta.env.VITE_FABRIC_TENANT_ID ?? import.meta.env.VITE_RAYFIN_TENANT_ID) as string | undefined
const msal = clientId && tenant ? new PublicClientApplication({
  auth: { clientId, authority: `https://login.microsoftonline.com/${tenant}`, redirectUri: location.origin },
  cache: { cacheLocation: 'sessionStorage' },
}) : undefined
let initialized: Promise<void> | undefined
const scopes = ['https://api.fabric.microsoft.com/Item.Read.All']
const API = 'https://api.fabric.microsoft.com'
const GUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i

export async function readOntology(contract: Contract): Promise<{ live: OntologyContract; hash: string }> {
  if (!msal) throw new Error('Fabric Entra configuration is missing. Import/edit drafts offline; live verification requires the configured Fabric app.')
  const { workspaceId, itemId } = contract.ontology
  if (!GUID.test(workspaceId) || !GUID.test(itemId)) throw new Error('Enter the target workspace and ontology item GUIDs.')
  await (initialized ??= msal.initialize())
  const accounts = msal.getAllAccounts()
  const account = msal.getActiveAccount() ?? (accounts.length === 1 ? accounts[0] : undefined)
  const result = account
    ? await msal.acquireTokenSilent({ scopes, account }).catch(error => {
      if (error?.name === 'InteractionRequiredAuthError') return msal.acquireTokenPopup({ scopes, account })
      throw error
    })
    : await msal.acquireTokenPopup({ scopes })
  msal.setActiveAccount(result.account)
  const headers = { Authorization: `Bearer ${result.accessToken}`, 'x-ms-fabric-skill': 'e2e-medallion-architecture' }
  const request = async (url: string, method = 'GET') => {
    const parsed = new URL(url)
    if (parsed.origin !== API || parsed.username || parsed.password || parsed.hash) throw new Error('Untrusted Fabric operation URL.')
    const response = await fetch(url, {
      method, headers: { ...headers, 'Content-Type': 'application/json' },
      ...(method === 'POST' ? { body: '{}' } : {}), signal: AbortSignal.timeout(30_000), redirect: 'error',
    })
    if (!response.ok) throw new Error(`Fabric ontology read failed (${response.status}). Verify permissions and item identity.`)
    return response
  }
  const itemUrl = `${API}/v1/workspaces/${workspaceId}/ontologies/${itemId}`
  const metadata = await (await request(itemUrl)).json() as { displayName: string; properties?: { generation?: unknown } }
  if (metadata.properties?.generation !== 2) throw new Error('A live numeric generation-2 ontology is required. No legacy fallback is permitted.')
  const response = await request(`${API}/v1/workspaces/${workspaceId}/items/${itemId}/getDefinition`, 'POST')
  let definition: OntologyDefinition
  if (response.status === 202) {
    const location = response.headers.get('Location')
    if (!location) throw new Error('Fabric definition operation returned no Location.')
    const deadline = Date.now() + 120_000
    for (;;) {
      if (Date.now() >= deadline) throw new Error('Ontology definition read timed out.')
      const state = await (await request(location)).json() as { status?: string }
      if (state.status === 'Succeeded') break
      if (state.status === 'Failed' || state.status === 'Cancelled') throw new Error(`Ontology definition read ${state.status}.`)
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
    const url = new URL(location)
    url.pathname = `${url.pathname.replace(/\/$/, '')}/result`
    definition = await (await request(url.href)).json() as OntologyDefinition
  } else definition = await response.json() as OntologyDefinition
  const live = parseOntologyContract(itemId, metadata.displayName, definition, metadata.properties.generation)
  return { live, hash: await definitionFingerprint(definition.definition?.parts ?? []) }
}
