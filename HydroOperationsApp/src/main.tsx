import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import AppV2 from './AppV2.tsx'

const url = new URL(window.location.href)
const ui = url.searchParams.get('ui') === 'v2' ? 'v2' : 'v1'
if (url.searchParams.get('ui') !== ui) {
  url.searchParams.set('ui', ui)
  window.history.replaceState({}, '', url)
}

createRoot(document.getElementById('root')!).render(
  ui === 'v2' ? <AppV2 /> : <App />,
)
