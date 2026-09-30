export class OntologyCacheInvalidatedError extends Error {
  constructor() { super('Ontology discovery changed during refresh. Refresh again to load the current workspace.') }
}

/** Failed/absent refreshes invalidate previous data; old in-flight requests cannot repopulate a cleared cache. */
export function createOntologyCache<T>(ttlMs: number) {
  let revision = 0
  let cached: { key: string; expiresAt: number; value: T } | undefined
  let pending: { key: string; promise: Promise<T | null> } | undefined
  return {
    clear() { revision++; cached = undefined; pending = undefined },
    async read(key: string, force: boolean, load: () => Promise<T | null>): Promise<T | null> {
      if (pending?.key === key) return pending.promise
      if (!force && cached?.key === key && cached.expiresAt > Date.now()) return cached.value
      cached = undefined
      const current = ++revision
      const promise = Promise.resolve().then(load).then(value => {
        if (revision !== current) throw new OntologyCacheInvalidatedError()
        if (value !== null) cached = { key, expiresAt: Date.now() + ttlMs, value }
        return value
      }).finally(() => {
        if (revision === current) pending = undefined
      })
      pending = { key, promise }
      return promise
    },
  }
}
