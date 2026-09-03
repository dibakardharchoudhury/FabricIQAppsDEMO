export function createSingleFlight<TArgs extends unknown[], TResult>(operation: (...args: TArgs) => Promise<TResult>) {
  let active: Promise<TResult> | undefined
  return (...args: TArgs): Promise<TResult> => {
    if (active) return active
    const current = operation(...args)
    active = current
    void current.finally(() => {
      if (active === current) active = undefined
    }).catch(() => undefined)
    return current
  }
}