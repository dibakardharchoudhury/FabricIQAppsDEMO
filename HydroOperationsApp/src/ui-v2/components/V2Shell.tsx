import { useEffect, useState } from 'react'
import { CheckCircle2, Factory } from 'lucide-react'
import { V2_TABS, resolveV2Tab, type V2Tab } from '../navigation'
import { HydroOperationsDataProvider } from '../hooks/useHydroOperationsData'

const BUILD_STAMP = new Date(__BUILD_TIME__).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const UI_CREDIT_LINE_1 = (import.meta.env.VITE_RAYFIN_UI_CREDIT_LINE_1 as string | undefined)?.trim()
const UI_CREDIT_LINE_2 = (import.meta.env.VITE_RAYFIN_UI_CREDIT_LINE_2 as string | undefined)?.trim()

function tabFromLocation(): V2Tab {
  return resolveV2Tab(new URLSearchParams(window.location.search).get('tab'))
}

function setTabInUrl(tab: V2Tab) {
  const url = new URL(window.location.href)
  url.searchParams.delete('ui')
  url.searchParams.set('tab', tab.id)
  window.history.pushState({}, '', url)
}

export function V2Shell() {
  const [activeTab, setActiveTab] = useState<V2Tab>(() => tabFromLocation())
  const ActivePage = activeTab.Page

  useEffect(() => {
    const handlePopState = () => setActiveTab(tabFromLocation())
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const selectTab = (tab: V2Tab) => {
    setActiveTab(tab)
    setTabInUrl(tab)
  }

  return <div className="v2-shell">
    <header className="v2-topbar">
      <div className="v2-brand"><span className="v2-brand-mark"><Factory size={18} /></span><div><strong>Hydro Operations</strong><small>Microsoft Fabric</small></div></div>
      <div className="v2-header-status">
        <span className="v2-source-chip connected"><CheckCircle2 size={14} />UI v2 shell</span>
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
        {V2_TABS.map(tab => {
          const Icon = tab.icon
          const active = tab.id === activeTab.id
          return <button key={tab.id} className={active ? 'v2-tab active' : 'v2-tab'} type="button" aria-current={active ? 'page' : undefined} onClick={() => selectTab(tab)}>
            <Icon size={15} />
            <span>{tab.label}</span>
          </button>
        })}
      </div>
    </nav>

    <HydroOperationsDataProvider>
      <main className="v2-main">
        <ActivePage />
      </main>
    </HydroOperationsDataProvider>
  </div>
}