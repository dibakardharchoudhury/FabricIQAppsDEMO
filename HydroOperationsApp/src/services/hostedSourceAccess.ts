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
    super(`Sign-in is required for ${resources.join(', ')}. Retry your question or approval to continue sign-in. No agent request was sent.`)
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
    async acquire(interactive = false): Promise<HostedAccessTokens> {
      let results = await inspect()
      if (interactive) {
        for (const requirement of requirements) {
          if (results.find(result => result.kind === requirement.kind)?.token) continue
          await provider.popup(requirement.scopes)
          results = await inspect()
          if (!results.find(result => result.kind === requirement.kind)?.token) {
            throw new HostedSourceAccessError(results.filter(result => !result.token).map(result => result.label))
          }
        }
      }
      const missing = results.filter(result => !result.token)
      if (missing.length) throw new HostedSourceAccessError(missing.map(result => result.label))
      const tokens: HostedAccessTokens = { fabric: '', foundry: '', graphql: '', kusto: '' }
      for (const result of results) {
        if (!result.token) throw new HostedSourceAccessError([result.label])
        tokens[result.kind] = result.token
      }
      return tokens
    },
  }
}
