import { createRoot } from 'react-dom/client'
import './index.css'
import { RootApp } from './RootApp.tsx'

const url = new URL(window.location.href)
const ui = url.searchParams.get('ui') === 'v2' ? 'v2' : 'v1'
if (url.searchParams.get('ui') !== ui) {
  url.searchParams.set('ui', ui)
  window.history.replaceState({}, '', url)
}

createRoot(document.getElementById('root')!).render(
  <RootApp ui={ui} />,
)
