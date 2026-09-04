import { useCallback, useEffect, useRef, useState } from 'react'
import {
  EmbedManager,
  ItemType,
  KQLDashboardEmbedClient,
  ViewMode,
  type KQLDashboardEmbedConfiguration,
} from '@microsoft/fabric-embed'
import { fabricEmbedToken, getDashboardEmbedTarget, type DashboardEmbedTarget } from '../../../services/fabric'
import { TelemetryEmptyPanel } from './TelemetryCards'

const embedManager = new EmbedManager({ embedClientClasses: [KQLDashboardEmbedClient] })

type State =
  | { kind: 'loading' }
  | { kind: 'consent' }
  | { kind: 'missing' }
  | { kind: 'ready'; target: DashboardEmbedTarget; token: string }
  | { kind: 'error'; message: string }

export function TelemetryDashboardPanel() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [attempt, setAttempt] = useState({ count: 0, interactive: false })
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    getDashboardEmbedTarget(attempt.interactive)
      .then(async (target): Promise<State> => {
        if (!target) return { kind: 'missing' }
        const token = await fabricEmbedToken(attempt.interactive)
        return token ? { kind: 'ready', target, token } : { kind: 'consent' }
      })
      .then(next => { if (!cancelled) setState(next) })
      .catch((error: unknown) => {
        if (cancelled) return
        setState({ kind: 'error', message: error instanceof Error ? error.message : 'Could not load the Real-Time Dashboard.' })
      })
    return () => { cancelled = true }
  }, [attempt])

  // Popups are only allowed from a user gesture, so consent is never requested on mount.
  const retry = useCallback(() => {
    setState({ kind: 'loading' })
    setAttempt(current => ({ count: current.count + 1, interactive: true }))
  }, [])

  useEffect(() => {
    const element = host.current
    if (state.kind !== 'ready' || !element) return

    const config: KQLDashboardEmbedConfiguration = {
      accessToken: { token: state.token },
      itemType: ItemType.KQLDashboard,
      itemId: state.target.itemId,
      workspaceId: state.target.workspaceId,
      settings: { viewMode: ViewMode.View },
      eventHooks: {
        // Without this the embed breaks as soon as the initial token expires.
        accessTokenProvider: {
          callback: async input => {
            const token = await fabricEmbedToken(false, input?.scopes)
            if (!token) throw new Error('Fabric consent expired.')
            return { token }
          },
        },
      },
    }

    const client = embedManager.embed(element, config)
    client.on('error', event => console.warn('Fabric embed error.', event))

    return () => {
      client.off()
      element.replaceChildren()
    }
  }, [state])

  if (state.kind === 'missing') {
    return <TelemetryEmptyPanel
      title="No Real-Time Dashboard in this workspace"
      text="Run RTI_012_build_basic_telemetry_dashboard (or the whole Pipe_Setup) to provision it, then reload."
    />
  }

  if (state.kind === 'consent' || state.kind === 'error') {
    return <section className="v2-embed-panel">
      <div className="v2-embed-state">
        <p>{state.kind === 'error' ? state.message : 'Fabric needs your permission to embed the Real-Time Dashboard.'}</p>
        <button type="button" className="v2-primary-action" onClick={retry}>
          {state.kind === 'error' ? 'Retry' : 'Grant access'}
        </button>
      </div>
    </section>
  }

  return <section className="v2-embed-panel">
    {state.kind === 'loading' && <div className="v2-embed-state"><p>Loading the Real-Time Dashboard...</p></div>}
    <div ref={host} className="v2-embed-frame" role="region" aria-label="Fabric Real-Time Dashboard" />
  </section>
}
