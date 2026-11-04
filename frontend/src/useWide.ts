// Whether the screen is wide enough for the week as a table of days and meals (64rem, 1024px
// at the default text size). Without matchMedia, as in a test, it is.

import { useSyncExternalStore } from 'react'

const QUERY = '(min-width: 64rem)'

function list(): MediaQueryList | null {
  return typeof window.matchMedia === 'function' ? window.matchMedia(QUERY) : null
}

function subscribe(onChange: () => void): () => void {
  const media = list()
  media?.addEventListener('change', onChange)
  return () => media?.removeEventListener('change', onChange)
}

export function useWide(): boolean {
  return useSyncExternalStore(subscribe, () => list()?.matches ?? true)
}
