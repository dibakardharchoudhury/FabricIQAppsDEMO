import { useEffect, useState } from 'react'
import { Database, Factory, Radio, RefreshCw } from 'lucide-react'
import { V1_TABS, resolveV1Tab, type V1Tab } from '../navigation'
import { HydroOperationsDataProvider, useHydroOperationsData } from '../../ui-shared/hooks/useHydroOperationsData'

const BUILD_STAMP = new Date(__BUILD_TIME__).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const UI_CREDIT_LINE_1 = (import.meta.env.VITE_RAYFIN_UI_CREDIT_LINE_1 as string | undefined)?.trim()
const UI_CREDIT_LINE_2 = (import.meta.env.VITE_RAYFIN_UI_CREDIT_LINE_2 as string | undefined)?.trim()

function tabFromLocation(): V1Tab {
  return resolveV1Tab(new URLSearchParams(window.location.search).get('tab'))
}

function setTabInUrl(tab: V1Tab) {
  const url = new URL(window.location.href)
  url.searchParams.set('ui', 'v1')
  url.searchParams.set('tab', tab.id)
  window.history.pushState({}, '', url)
}

export function V1Shell() {
  return <HydroOperationsDataProvider><V1ShellContent /></HydroOperationsDataProvider>
}

function V1ShellContent() {
  const data = useHydroOperationsData()
  const [activeTab, setActiveTab] = useState<V1Tab>(() => tabFromLocation())
  const ActivePage = activeTab.Page
  const telemetryLive = data.telemetry.length > 0
  const eventTimes = data.telemetry.map(reading => Date.parse(reading.eventTime)).filter(time => !Number.isNaN(time))
  const newestEventMs = eventTimes.length ? Math.max(...eventTimes) : 0
  const oldestEventMs = eventTimes.length ? Math.min(...eventTimes) : 0
  const freshCount = eventTimes.filter(time => data.now - time < 60_000).length
  const stidLabel = data.stidState === 'connected' ? 'STID connected' : data.stidState === 'loading' ? 'Connecting...' : 'Connect STID'
  const telemetryLabel = data.telemetryState === 'loading' ? 'Connecting...' : telemetryLive ? `${data.telemetry.length} signals` : data.telemetryState === 'connected' ? 'No recent events' : 'Connect telemetry'

  useEffect(() => {
    const handlePopState = () => setActiveTab(tabFromLocation())
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const selectTab = (tab: V1Tab) => {
    setActiveTab(tab)
    setTabInUrl(tab)
  }

  return <div className="v2-shell">
    <header className="v2-topbar">
      <div className="v2-brand"><span className="v2-brand-mark"><Factory size={18} /></span><div><strong>Hydro Operations</strong><small>Microsoft Fabric</small></div></div>
      <div className="v2-header-status source-actions">
        <button className={data.stidState === 'connected' ? 'source-chip connected' : 'source-chip'} onClick={() => void data.actions.connectStid()} title="Step 4 · Load governed facility & asset metadata from the Lakehouse GraphQL API (publish it first via Seed & provision).">4 · <Database size={14} />{stidLabel}</button>
        <div className="telemetry-source">
          <button className={telemetryLive ? 'source-chip connected' : 'source-chip'} onClick={() => void data.actions.connectTelemetry()} title="Step 5 · Read the latest OPC UA signals from the Eventhouse (start the stream first).">5 · <Radio size={14} />{telemetryLabel}</button>
          {telemetryLive && <span className={`live-pill ${data.telemetryStatus}`} title={`Polling every 10s · ${freshCount}/${eventTimes.length} signals fresh (<60s)${newestEventMs ? ` · newest ${new Date(newestEventMs).toLocaleTimeString()}` : ''}${oldestEventMs ? ` · oldest ${new Date(oldestEventMs).toLocaleTimeString()}` : ''}`}><i className="live-dot" />{data.telemetryStatusLabel}<em>· {data.telemetryAgeLabel}</em></span>}
          {telemetryLive && <button className="refresh-btn" onClick={() => void data.actions.connectTelemetry()} disabled={data.telemetryState === 'loading'} title="Refresh live telemetry now"><RefreshCw size={14} className={data.telemetryState === 'loading' ? 'spin' : undefined} /></button>}
        </div>
      </div>
      <div className="v2-top-actions">
        {(UI_CREDIT_LINE_1 || UI_CREDIT_LINE_2) && <div className="v2-credits" aria-label="Application credits">
          {UI_CREDIT_LINE_1 && <span>{UI_CREDIT_LINE_1}</span>}
          {UI_CREDIT_LINE_2 && <span>{UI_CREDIT_LINE_2}</span>}
        </div>}
        <span className="v2-app-version" title={`Version ${__APP_VERSION__}${__BUILD_COMMIT__ ? ` · ${__BUILD_COMMIT__}` : ''} · built ${BUILD_STAMP}`}><strong>v{__APP_VERSION__}</strong><small>{BUILD_STAMP}</small></span>
      </div>
    </header>

    <nav className="v2-tabs" aria-label="Hydro Operations domains">
      <div className="v2-tabs-inner">
        {V1_TABS.map(tab => {
          const Icon = tab.icon
          const active = tab.id === activeTab.id
          return <button key={tab.id} className={active ? 'v2-tab active' : 'v2-tab'} type="button" aria-current={active ? 'page' : undefined} onClick={() => selectTab(tab)}>
            <Icon size={15} />
            <span>{tab.label}</span>
          </button>
        })}
      </div>
    </nav>

    <main className="v2-main">
      <ActivePage />
    </main>
  </div>
}
