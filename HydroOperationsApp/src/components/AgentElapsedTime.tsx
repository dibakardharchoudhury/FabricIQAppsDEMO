import { useEffect, useState } from 'react'

export function AgentElapsedTime({ startedAt, elapsedMs, running }: {
  startedAt?: number
  elapsedMs?: number
  running: boolean
}) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!running || startedAt === undefined) return
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [running, startedAt])
  const elapsed = running && startedAt !== undefined ? Math.max(0, now - startedAt) : elapsedMs
  return <span className="agent-elapsed-time" aria-label={running ? 'Elapsed time, running' : 'Elapsed time'}>
    {elapsed === undefined ? '--' : `${(elapsed / 1000).toFixed(1)}s`}
  </span>
}
