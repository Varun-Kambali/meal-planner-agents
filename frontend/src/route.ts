// The screen is the part of the address after "#/", so the app is one file on any static host
// and a reload keeps the screen. A "#" not followed by "/" (the skip link, a field's id) is not
// a screen and leaves the screen as it is.

import { useEffect, useState } from 'react'

import type { DayKey } from './api'
import { isDay } from './words'

export type Route =
  | { name: 'home' }
  | { name: 'plan' }
  | { name: 'week' }
  | { name: 'recipe'; day: DayKey; slotId: string }
  | { name: 'recipes' }
  | { name: 'missing' }

export function parseRoute(hash: string): Route | null {
  if (hash === '' || hash === '#') return { name: 'home' }
  if (!hash.startsWith('#/')) return null
  let parts: string[]
  try {
    parts = hash.slice(2).split('/').map(decodeURIComponent)
  } catch {
    // A "%" not followed by two hex digits, say: no screen has such an address.
    return { name: 'missing' }
  }
  const [screen, day, slotId] = parts
  if (parts.length === 1 && screen === '') return { name: 'home' }
  if (parts.length === 1 && screen === 'plan') return { name: 'plan' }
  if (parts.length === 1 && screen === 'week') return { name: 'week' }
  if (parts.length === 1 && screen === 'recipes') return { name: 'recipes' }
  if (parts.length === 3 && screen === 'recipe' && day !== undefined && isDay(day) && slotId) {
    return { name: 'recipe', day, slotId }
  }
  return { name: 'missing' }
}

export function recipeHref(day: DayKey, slotId: string): string {
  return `#/recipe/${day}/${encodeURIComponent(slotId)}`
}

// The screen the address names, and how many times it has changed since the page opened, so
// the shell can move the focus to a new screen but not on the first.
export function useRoute(): { route: Route; moves: number } {
  const [current, setCurrent] = useState(() => ({
    route: parseRoute(window.location.hash) ?? { name: 'home' as const },
    moves: 0,
  }))
  useEffect(() => {
    const changed = (): void => {
      const route = parseRoute(window.location.hash)
      if (route !== null) setCurrent((before) => ({ route, moves: before.moves + 1 }))
    }
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  return current
}
