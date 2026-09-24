import { useEffect, useRef, useState } from 'react'
import { Activity } from 'lucide-react'
import { queryLiveGridFrequency } from '../../../services/energyMap'
import { FREQUENCY_STALE_MS, frequencyRetryDelay, type LiveFrequencyReading } from '../../liveFrequencyModel'

export function LiveGridFrequencyTile() {
  const tile = useRef<HTMLElement>(null)
  const [reading, setReading] = useState<LiveFrequencyReading>()
  const [error, setError] = useState<string>()
  const [paused, setPaused] = useState(true)
  const [now, setNow] = useState(Date.now)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let disposed = false
    let inView = false
    let failures = 0
    let nextPoll: number | undefined
    let current: AbortController | undefined
    const visible = () => !disposed && inView && document.visibilityState === 'visible'
    const active = () => visible() && navigator.onLine
    const clearPoll = () => {
      if (nextPoll !== undefined) window.clearTimeout(nextPoll)
      nextPoll = undefined
    }
    const poll = async () => {
      if (!active() || current) return
      clearPoll()
      const controller = new AbortController()
      current = controller
      let timedOut = false
      const timeout = window.setTimeout(() => {
        timedOut = true
        if (active() && current === controller) setError('Live frequency request timed out.')
        controller.abort()
      }, 12_000)
      try {
        const sample = await queryLiveGridFrequency(controller.signal)
        if (!active() || current !== controller) return
        if (timedOut) throw new Error('Live frequency request timed out.')
        setReading(previous => previous && previous.observedAtMs > sample.observedAtMs ? previous : sample)
        setNow(Date.now())
        setError(undefined)
        failures = 0
      } catch (reason) {
        if (!active() || current !== controller) return
        console.error('Live grid frequency update failed.', reason)
        setError(timedOut ? 'Live frequency request timed out.' : reason instanceof Error ? reason.message : 'Live frequency is unavailable.')
        failures++
      } finally {
        window.clearTimeout(timeout)
        if (current === controller) {
          current = undefined
          if (active()) nextPoll = window.setTimeout(() => void poll(), frequencyRetryDelay(failures))
        }
      }
    }
    const updateActivity = () => {
      const enabled = active()
      setPaused(!enabled)
      setNow(Date.now())
      if (!enabled) {
        clearPoll()
        current?.abort()
        current = undefined
      } else if (!current && nextPoll === undefined) {
        void poll()
      }
    }
    const observer = new IntersectionObserver(entries => {
      inView = entries.some(entry => entry.isIntersecting)
      updateActivity()
    })
    if (tile.current) observer.observe(tile.current)
    document.addEventListener('visibilitychange', updateActivity)
    window.addEventListener('online', updateActivity)
    window.addEventListener('offline', updateActivity)
    const clock = window.setInterval(() => { if (visible()) setNow(Date.now()) }, 1000)
    return () => {
      disposed = true
      clearPoll()
      current?.abort()
      observer.disconnect()
      window.clearInterval(clock)
      document.removeEventListener('visibilitychange', updateActivity)
      window.removeEventListener('online', updateActivity)
      window.removeEventListener('offline', updateActivity)
    }
  }, [retry])

  const ageMs = reading ? Math.max(0, now - reading.observedAtMs) : undefined
  const stale = ageMs === undefined || ageMs > FREQUENCY_STALE_MS
  const state = paused ? 'Paused' : error ? 'Update failed' : !reading ? 'Connecting...' : stale ? 'Source delayed' : 'Live'
  return <section ref={tile} className="energy-map-frequency-tile" aria-label="Grid frequency">
    <div><Activity size={19} /><h2>Grid frequency</h2><span className={!paused && !error && !stale ? 'energy-map-live' : 'energy-map-stale'}>{state}</span></div>
    <strong>{reading ? `${reading.hz.toFixed(3)} Hz` : error ? 'Unavailable' : 'Connecting...'}</strong>
    <span className={stale || error ? 'energy-map-stale' : ''}>{ageMs === undefined ? 'Waiting for a source sample' : `Observed ${Math.floor(ageMs / 1000)}s ago${stale ? ' - stale reading' : ''}`}</span>
    <small>Statnett live samples through Fabric, refreshed about every 5 seconds while visible. No continuous collection or history is stored.</small>
    {error && <p role="alert">{error} {reading ? 'Last successful reading is retained, not replaced with a simulated value.' : 'No live reading has been received.'}</p>}
    {error && <button type="button" className="energy-map-live-retry" onClick={() => setRetry(value => value + 1)}>Retry live frequency</button>}
  </section>
}
