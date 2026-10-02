import AppV1 from './AppV1.tsx'
import AppV2 from './AppV2.tsx'

export function RootApp({ ui }: { ui: 'v1' | 'v2' }) {
  return ui === 'v2' ? <AppV2 /> : <AppV1 />
}
