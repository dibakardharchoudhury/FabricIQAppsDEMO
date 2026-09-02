import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import AppV2 from './AppV2.tsx'

const useV2 = new URLSearchParams(window.location.search).get('ui') === 'v2'

createRoot(document.getElementById('root')!).render(
  useV2 ? <AppV2 /> : <App />,
)
