export type HostedTokenKind = 'fabric' | 'foundry' | 'graphql' | 'kusto'
export type HostedAccessRequirement = { kind: HostedTokenKind; label: string; scopes: string[] }
export type HostedAccessTokens = Record<HostedTokenKind, string>
type TokenProvider = {
  silent: (scopes: string[]) => Promise<string | null>
  popup: (scopes: string[]) => Promise<string>
}

export class HostedSourceAccessError extends Error {
  readonly resources: string[]
  constructor(resources: string[]) {
    super(`Connect agent access to authorize: ${resources.join(', ')}. No agent request was sent.`)
    this.name = 'HostedSourceAccessError'
    this.resources = resources
  }
}

export function createHostedSourceAccess(requirements: HostedAccessRequirement[], provider: TokenProvider) {
  const kinds: HostedTokenKind[] = ['fabric', 'foundry', 'graphql', 'kusto']
  if (requirements.length !== kinds.length || kinds.some(kind => requirements.filter(item => item.kind === kind).length !== 1)) {
    throw new Error('Hosted access requires exactly one authorization requirement per source audience.')
  }
  const inspect = async () => Promise.all(requirements.map(async requirement => ({
    ...requirement, token: await provider.silent(requirement.scopes),
  })))

  return {
    async acquire(): Promise<HostedAccessTokens> {
      const results = await inspect()
      const missing = results.filter(result => !result.token)
      if (missing.length) throw new HostedSourceAccessError(missing.map(result => result.label))
      const tokens: HostedAccessTokens = { fabric: '', foundry: '', graphql: '', kusto: '' }
      for (const result of results) {
        if (!result.token) throw new HostedSourceAccessError([result.label])
        tokens[result.kind] = result.token
      }
      return tokens
    },
    async connectNext(): Promise<string[]> {
      const results = await inspect()
      const next = results.find(result => !result.token)
      if (!next) return []
      await provider.popup(next.scopes)
      // Recheck the lease instead of treating popup completion as proof of usable access.
      const refreshed = await inspect()
      return refreshed.filter(result => !result.token).map(result => result.label)
    },
  }
}
