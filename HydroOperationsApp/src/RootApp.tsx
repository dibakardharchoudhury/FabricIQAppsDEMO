import { lazy, Suspense } from 'react'

const AppV1 = lazy(() => import('./AppV1.tsx'))
const AppV2 = lazy(() => import('./AppV2.tsx'))

export function RootApp({ ui }: { ui: 'v1' | 'v2' }) {
  return <Suspense fallback={<div className="app-bootstrap"><span /><strong>Hydro Operations</strong><small>Loading application…</small></div>}>
    {ui === 'v2' ? <AppV2 /> : <AppV1 />}
  </Suspense>
}
