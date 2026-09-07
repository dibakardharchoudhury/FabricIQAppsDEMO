import { useCallback, useMemo, useState } from 'react'

/** Expanded-node ids: the path to the active item opens by default, user toggles override it. */
export function useTreeExpansion(revealPath: string[]) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>({})
  const revealed = useMemo(() => new Set(revealPath), [revealPath])

  const isExpanded = useCallback((id: string) => overrides[id] ?? revealed.has(id), [overrides, revealed])
  const toggle = useCallback((id: string) => setOverrides(current => ({ ...current, [id]: !(current[id] ?? revealed.has(id)) })), [revealed])

  return { isExpanded, toggle }
}