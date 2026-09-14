import { useCallback, useEffect, useState } from 'react'

export type AppTheme = 'light' | 'dark'

const STORAGE_KEY = 'hydro.theme'
const THEME_EVENT = 'hydro-theme-change'

function currentTheme(): AppTheme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setThemeState] = useState<AppTheme>(currentTheme)

  useEffect(() => {
    const sync = () => setThemeState(currentTheme())
    window.addEventListener(THEME_EVENT, sync)
    return () => window.removeEventListener(THEME_EVENT, sync)
  }, [])

  const setTheme = useCallback((next: AppTheme) => {
    document.documentElement.dataset.theme = next
    document.documentElement.style.colorScheme = next
    try { localStorage.setItem(STORAGE_KEY, next) } catch { /* storage unavailable */ }
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', next === 'dark' ? '#111827' : '#f5f7fb')
    window.dispatchEvent(new Event(THEME_EVENT))
  }, [])

  return { theme, setTheme }
}