import { InteractionRequiredAuthError, type PublicClientApplication } from '@azure/msal-browser'

type TokenClient = Pick<PublicClientApplication,
  'getActiveAccount' | 'getAllAccounts' | 'setActiveAccount' | 'acquireTokenSilent' | 'acquireTokenPopup'>

export function createEntraTokens(client: TokenClient, initialize: () => Promise<void>, tenantId: string) {
  const selectAccount = () => {
    const active = client.getActiveAccount()
    if (active?.tenantId === tenantId) return active
    const candidates = client.getAllAccounts().filter(account => account.tenantId === tenantId)
    return candidates.length === 1 ? candidates[0] : undefined
  }

  const silent = async (scopes: string[], forceRefresh = false): Promise<string | null> => {
    await initialize()
    const account = selectAccount()
    if (!account) return null
    try {
      const result = await client.acquireTokenSilent({ account, scopes, forceRefresh })
      if (!result.accessToken) throw new Error('Entra returned no access token.')
      return result.accessToken
    } catch (error) {
      if (error instanceof InteractionRequiredAuthError) return null
      throw error
    }
  }

  const pending = new Map<string, Promise<string>>()
  let interactiveQueue: Promise<void> = Promise.resolve()
  const popup = async (scopes: string[]): Promise<string> => {
    await initialize()
    const key = JSON.stringify([selectAccount()?.homeAccountId, [...new Set(scopes)].sort()])
    const existing = pending.get(key)
    if (existing) return existing
    const current = interactiveQueue.then(async () => {
      const cached = await silent(scopes)
      if (cached) return cached
      const account = selectAccount()
      const result = await client.acquireTokenPopup({
        scopes, account,
        ...(!account && client.getAllAccounts().filter(item => item.tenantId === tenantId).length > 1
          ? { prompt: 'select_account' } : {}),
      })
      if (result.account?.tenantId !== tenantId || !result.accessToken) {
        throw new Error('Entra sign-in did not return a token for the configured tenant.')
      }
      client.setActiveAccount(result.account)
      return result.accessToken
    })
    pending.set(key, current)
    interactiveQueue = current.then(() => undefined, () => undefined)
    void current.finally(() => {
      if (pending.get(key) === current) pending.delete(key)
    }).catch(() => undefined)
    return current
  }
  return { silent, popup }
}
