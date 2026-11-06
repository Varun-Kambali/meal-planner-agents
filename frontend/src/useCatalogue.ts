// The ingredient catalogue (GET /v1/catalogue), held by the shell for the screens that use it:
// the plan form's food lists and the recipe page's steps. Every /v1/ route shares one rate
// limit, so it is asked for once per page, by the first screen that needs it, and not again when
// a screen opens again. A list that failed is asked for again the next time a screen needs it,
// and the plan form can ask again on the person's say.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { catalogue as loadCatalogue } from './api'
import type { CatalogueRow } from './types'
import { messageOf } from './words'

export type CatalogueState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; rows: CatalogueRow[] }
  | { status: 'failed'; message: string }

export interface SharedCatalogue {
  state: CatalogueState
  // Asks for the list unless it is loading or has loaded.
  need: () => void
  // Asks for it again, whatever state it is in.
  reload: () => void
}

export function useCatalogue(): SharedCatalogue {
  const [state, setState] = useState<CatalogueState>({ status: 'idle' })
  // The status as of the last call, for `need`, which runs before a render shows the new one.
  const status = useRef<CatalogueState['status']>('idle')
  const request = useRef<AbortController | null>(null)

  const reload = useCallback((): void => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    status.current = 'loading'
    setState({ status: 'loading' })
    loadCatalogue({}, { signal: controller.signal }).then(
      (rows) => {
        if (controller.signal.aborted) return
        status.current = 'ready'
        setState({ status: 'ready', rows })
      },
      (error: unknown) => {
        if (controller.signal.aborted) return
        status.current = 'failed'
        setState({ status: 'failed', message: messageOf(error) })
      },
    )
  }, [])

  const need = useCallback((): void => {
    if (status.current === 'idle' || status.current === 'failed') reload()
  }, [reload])

  // A call stopped as the page goes leaves nothing loaded, so a page that comes back (React does
  // this once in development) asks again.
  useEffect(
    () => () => {
      request.current?.abort()
      if (status.current === 'loading') status.current = 'idle'
    },
    [],
  )

  return useMemo(() => ({ state, need, reload }), [state, need, reload])
}

// Each catalogue id with its name, once the list has loaded; none before.
export function foodNames(state: CatalogueState): Map<string, string> {
  return new Map(state.status === 'ready' ? state.rows.map((row) => [row.id, row.name]) : [])
}
