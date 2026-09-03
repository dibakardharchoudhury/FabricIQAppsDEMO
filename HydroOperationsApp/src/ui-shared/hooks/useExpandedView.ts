import { useEffect, useState } from 'react'

export function useExpandedView() {
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!expanded) return
    const previousOverflow = document.body.style.overflow
    const restoreOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false)
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', restoreOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', restoreOnEscape)
    }
  }, [expanded])

  return { expanded, toggleExpanded: () => setExpanded(current => !current) }
}