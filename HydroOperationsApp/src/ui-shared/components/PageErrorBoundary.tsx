import { Component, type ErrorInfo, type ReactNode } from 'react'

export class PageErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Hydro page failed to load or render.', error, info.componentStack)
  }

  render() {
    if (this.state.failed) return <div role="alert" className="v2-page-loading">
      <p>This page could not load. Check your connection and reload to try again. Unsaved chat drafts will be lost.</p>
      <button type="button" onClick={() => window.location.reload()}>Reload app</button>
    </div>
    return this.props.children
  }
}
