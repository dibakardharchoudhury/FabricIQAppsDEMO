import { Moon, Sun, Users } from 'lucide-react'
import { useTheme } from '../hooks/useTheme'

const BUILD_STAMP = new Date(__BUILD_TIME__).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const DEFAULT_DEVELOPERS = 'Dibakar Dharchoudhury, Jon Olav Abeland, Gareth O Brien, Ondrej Spilka'
const DEFAULT_VTEAM = 'Alex Grubb, Gareth O Brien, Frode Odinsen, Jon Olav Abeland'

function names(value: string | undefined, prefix: string, fallback: string) {
  const text = value?.trim() || fallback
  return text.replace(new RegExp(`^${prefix}:?\\s*`, 'i'), '').split(',').map(item => item.trim()).filter(Boolean)
}

export function HeaderMeta() {
  const { theme, setTheme } = useTheme()
  const developers = names(import.meta.env.VITE_RAYFIN_UI_CREDIT_LINE_1 as string | undefined, 'Developers', DEFAULT_DEVELOPERS)
  if (!developers.some(name => name.toLowerCase() === 'ondrej spilka')) developers.push('Ondrej Spilka')
  const vteam = names(import.meta.env.VITE_RAYFIN_UI_CREDIT_LINE_2 as string | undefined, 'vTeam Hydro', DEFAULT_VTEAM)

  return <div className="v2-header-meta">
    <details className="v2-credit-menu">
      <summary title="View application credits"><Users size={15} /><span>Credits</span></summary>
      <div className="v2-credit-popover">
        <section><strong>Developers</strong><p>{developers.join(' · ')}</p></section>
        <section><strong>vTeam Hydro</strong><p>{vteam.join(' · ')}</p></section>
      </div>
    </details>
    <button className="v2-theme-toggle" type="button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>
      {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
    <span className="v2-app-version" title={`Version ${__APP_VERSION__}${__BUILD_COMMIT__ ? ` · ${__BUILD_COMMIT__}` : ''} · built ${BUILD_STAMP}`}><strong>v{__APP_VERSION__}</strong><small>{BUILD_STAMP}</small></span>
  </div>
}