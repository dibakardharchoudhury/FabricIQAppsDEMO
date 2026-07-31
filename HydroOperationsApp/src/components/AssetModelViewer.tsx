/* eslint-disable @typescript-eslint/no-namespace -- augmenting React.JSX for the model-viewer custom element requires namespaces */
import '@google/model-viewer'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Asset3DModelRecord } from '../services/rayfin'
import { twinStatus, twinValueText, ageLabel, freshnessOf, type TwinSignal } from '../twin'

// `<model-viewer>` is a framework-agnostic web component; declare it so TSX accepts it.
declare global {
  namespace React {
    namespace JSX {
      interface IntrinsicElements {
        'model-viewer': React.DetailedHTMLProps<
          React.HTMLAttributes<HTMLElement> & {
            ref?: React.Ref<HTMLElement>
            src?: string
            alt?: string
            poster?: string
            'camera-controls'?: boolean
            'auto-rotate'?: boolean
            'auto-rotate-delay'?: number
            'rotation-per-second'?: string
            'shadow-intensity'?: string
            'interaction-prompt'?: string
            'min-hotspot-opacity'?: string
            exposure?: string
            loading?: string
            reveal?: string
          },
          HTMLElement
        >
      }
    }
  }
}

type Vec = { x: number; y: number; z: number }
// model-viewer exposes these after the model's `load` event; typed loosely (optional).
type ModelViewerEl = HTMLElement & {
  getBoundingBoxCenter?: () => Vec
  getDimensions?: () => Vec
}

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5))

// Anchor each signal to a point on a cylinder around the model's actual bounding box, spread
// top→bottom by index and around the axis by the golden angle. This keeps the "part map" on-model
// for any GLB (real geometry, unknown scale) without hand-authored coordinates.
function hotspotPlacement(bounds: { center: Vec; dims: Vec }, index: number, count: number) {
  const { center, dims } = bounds
  const theta = index * GOLDEN_ANGLE
  const radius = 0.5 * Math.max(dims.x, dims.z, 0.001) * 0.72 + 0.001
  const y = center.y + (count <= 1 ? 0 : (0.5 - index / (count - 1)) * dims.y * 0.8)
  const x = center.x + radius * Math.cos(theta)
  const z = center.z + radius * Math.sin(theta)
  return { position: `${x} ${y} ${z}`, normal: `${Math.cos(theta)} 0 ${Math.sin(theta)}` }
}

// Renders the GLB with live, health-coded telemetry hotspots pinned to the model. Other formats
// fall back to a thumbnail/link in App.tsx.
const TREND: Record<'up' | 'down' | 'flat', string> = { up: '▲', down: '▼', flat: '→' }

export function AssetModelViewer({ model, signals, assetLabel, updatedAt }: { model: Asset3DModelRecord; signals: TwinSignal[]; assetLabel?: string; updatedAt?: number }) {
  const viewer = useRef<ModelViewerEl>(null)
  const [bounds, setBounds] = useState<{ center: Vec; dims: Vec } | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)

  // Track the previous poll's values so each hotspot can show a live rising/falling trend. We shift the
  // snapshot one poll behind by adjusting state during render when the incoming values change — the
  // React-recommended alternative to a ref (which can't be read during render) or a cascading effect.
  const [history, setHistory] = useState<{ key: string; prev: Map<string, number>; latest: Map<string, number> }>({ key: '', prev: new Map(), latest: new Map() })
  const signalsKey = signals.map((s) => `${s.nodeId}=${s.value ?? ''}`).join('|')
  if (history.key !== signalsKey) {
    const next = new Map<string, number>()
    for (const signal of signals) if (typeof signal.value === 'number') next.set(signal.nodeId, signal.value)
    setHistory((current) => ({ key: signalsKey, prev: current.latest, latest: next }))
  }
  const trends = useMemo(() => {
    const map = new Map<string, 'up' | 'down' | 'flat'>()
    for (const signal of signals) {
      const current = history.latest.get(signal.nodeId)
      const prev = history.prev.get(signal.nodeId)
      if (current !== undefined && prev !== undefined) map.set(signal.id, current > prev ? 'up' : current < prev ? 'down' : 'flat')
    }
    return map
  }, [signals, history])

  const counts = useMemo(() => {
    const c = { ok: 0, warn: 0, crit: 0, nodata: 0 }
    for (const signal of signals) c[twinStatus(signal)]++
    return c
  }, [signals])

  // Median reading time across this asset's hotspots — drives the live/stale badge in the twin header
  // (median, not newest, so one fresh hotspot can't hide a stalled asset). Fresh = within ~2 min.
  const eventMsSorted = useMemo(() => signals.map(s => (s.eventTime ? Date.parse(s.eventTime) : NaN)).filter(t => !Number.isNaN(t)).sort((a, b) => a - b), [signals])
  const medianSignalMs = eventMsSorted.length ? eventMsSorted[Math.floor((eventMsSorted.length - 1) / 2)] : 0
  const medianSignalIso = medianSignalMs ? new Date(medianSignalMs).toISOString() : undefined
  const twinFresh = ['live', 'recent'].includes(freshnessOf(medianSignalIso))

  // Read the model's real bounds once loaded. The parent remounts this component per model
  // (keyed on modelUrl), so state starts fresh — no synchronous reset needed here.
  useEffect(() => {
    const element = viewer.current
    if (!element) return
    const onLoad = () => {
      try {
        const center = element.getBoundingBoxCenter?.() ?? { x: 0, y: 0, z: 0 }
        const dims = element.getDimensions?.() ?? { x: 1, y: 1, z: 1 }
        setBounds({ center, dims })
      } catch {
        setBounds({ center: { x: 0, y: 0, z: 0 }, dims: { x: 1, y: 1, z: 1 } })
      }
    }
    element.addEventListener('load', onLoad)
    return () => element.removeEventListener('load', onLoad)
  }, [model.modelUrl])

  const placements = useMemo(
    () => (bounds ? signals.map((_, index) => hotspotPlacement(bounds, index, signals.length)) : []),
    [bounds, signals],
  )

  const active = signals.find(signal => signal.id === activeId)

  return (
    <div className="twin-stage">
      <div className="twin-live-head">
        <span className="twin-live-dot" />
        <strong>{assetLabel ?? 'Live twin'}</strong>
        <span className="twin-live-chips">
          <em className="ok" title="Healthy">{counts.ok}</em>
          <em className="warn" title="Uncertain">{counts.warn}</em>
          <em className="crit" title="Bad / open order">{counts.crit}</em>
        </span>
        {medianSignalMs
          ? <small className={`twin-live-age ${twinFresh ? 'fresh' : 'stale'}`} title={`Median reading ${new Date(medianSignalMs).toLocaleTimeString()}`}>{twinFresh ? 'Live' : 'Stale'} · {ageLabel(medianSignalIso)}</small>
          : updatedAt && <small title="Last telemetry refresh">{new Date(updatedAt).toLocaleTimeString()}</small>}
      </div>
      <model-viewer
        ref={viewer as React.Ref<HTMLElement>}
        className="asset-model-viewer"
        src={model.modelUrl}
        alt={model.modelName}
        poster={model.thumbnailUrl}
        camera-controls
        {...(activeId ? {} : { 'auto-rotate': true })}
        auto-rotate-delay={0}
        rotation-per-second="16deg"
        shadow-intensity="1"
        interaction-prompt="none"
        min-hotspot-opacity="0.28"
        exposure="1"
        loading="eager"
        reveal="auto"
      >
        {placements.map((placement, index) => {
          const signal = signals[index]
          const status = twinStatus(signal)
          return (
            <button
              key={signal.id}
              slot={`hotspot-${index}`}
              data-position={placement.position}
              data-normal={placement.normal}
              className={`twin-hotspot ${status}${activeId === signal.id ? ' selected' : ''}`}
              onClick={() => setActiveId(current => (current === signal.id ? null : signal.id))}
              title={signal.label}
            >
              <span className="twin-dot" />
              <span className="twin-pill">{twinValueText(signal)}</span>
            </button>
          )
        })}
      </model-viewer>

      {active && (
        <div className="twin-detail">
          <span className={`twin-detail-mark ${twinStatus(active)}`} />
          <div className="twin-detail-body">
            <strong>{active.label}</strong>
            <span className="twin-detail-val">
              {twinValueText(active)}
              {trends.get(active.id) && <span className={`twin-trend ${trends.get(active.id)}`}>{TREND[trends.get(active.id)!]}</span>}
            </span>
            <small>{active.nodeId}</small>
            <small>
              Quality: {active.quality ?? 'no recent event'}
              {active.hasOpenIssue ? ' · open work order' : ''}
            </small>
            <small className={`twin-detail-age ${freshnessOf(active.eventTime)}`}>
              <i />Updated {ageLabel(active.eventTime)}
            </small>
          </div>
          <button className="twin-detail-close" onClick={() => setActiveId(null)} title="Close" aria-label="Close">
            ×
          </button>
        </div>
      )}

      {!bounds && <div className="twin-loading">Loading 3D model…</div>}
    </div>
  )
}
