import { useEffect, useState } from 'react'
import { queryTelemetryHistory, type TelemetryHistoryPoint, type TelemetryHistoryRange } from '../../services/fabric'

export type TelemetryHistoryState = 'idle' | 'loading' | 'ready' | 'empty' | 'unavailable' | 'error'

export type TelemetryHistoryResult = {
  points: TelemetryHistoryPoint[]
  state: TelemetryHistoryState
  error?: string
}

const IDLE: TelemetryHistoryResult = { points: [], state: 'idle' }
const LOADING: TelemetryHistoryResult = { points: [], state: 'loading' }

type Loaded = TelemetryHistoryResult & { key: string }

export function useTelemetryHistory(opcuaNodeId: string | undefined, range: TelemetryHistoryRange): TelemetryHistoryResult {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const key = opcuaNodeId ? `${opcuaNodeId}::${range}` : ''

  useEffect(() => {
    if (!opcuaNodeId) return
    let cancelled = false
    queryTelemetryHistory(opcuaNodeId, range).then(rows => {
      if (cancelled) return
      if (rows === null) setLoaded({ key, points: [], state: 'unavailable' })
      else setLoaded({ key, points: rows, state: rows.length ? 'ready' : 'empty' })
    }).catch((error: unknown) => {
      if (cancelled) return
      setLoaded({ key, points: [], state: 'error', error: error instanceof Error ? error.message : 'Historical telemetry query failed.' })
    })
    return () => { cancelled = true }
  }, [key, opcuaNodeId, range])

  // Idle and loading are derived from the request key so the effect only ever writes settled results.
  if (!key) return IDLE
  return loaded?.key === key ? loaded : LOADING
}
