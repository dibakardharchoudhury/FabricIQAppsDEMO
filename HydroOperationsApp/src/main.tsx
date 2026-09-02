import { createRoot } from 'react-dom/client'
import './index.css'
import AppV2 from './AppV2.tsx'

const url = new URL(window.location.href)
if (url.searchParams.get('ui') !== 'v2') {
  url.searchParams.set('ui', 'v2')
  window.history.replaceState({}, '', url)
}

createRoot(document.getElementById('root')!).render(
  <AppV2 />,
)
