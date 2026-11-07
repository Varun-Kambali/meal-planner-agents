// The log's summary (POST /v1/summary), held by the shell for the day and week views. Every /v1/
// route shares one rate limit, so it is asked for when one of those views opens and needs it, and
// not again until the plan or the log changes: a saved meal makes it out of date, and the view
// that is open, or the next one to open, asks again. A summary that failed is asked for again on
// the person's say, or the next time a view opens.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { summarise } from './api'
import type { MealLog, PlanRules, WeekSummary } from './types'

export type SummaryState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; summary: WeekSummary }
  | { status: 'failed'; error: unknown }

export interface SharedSummary {
  // The summary of the plan and log as they are now; idle when none has been asked for.
  state: SummaryState
  // Asks for it unless it is loading or has loaded.
  need: () => void
  // Asks for it again, whatever state it is in.
  retry: () => void
}

// What was asked for: the state is held with the plan and log it is for, so a new plan or log
// (each save makes new ones) leaves nothing that could pass for its summary.
interface Held {
  rules: PlanRules | null
  log: MealLog | null
  state: SummaryState
}

const IDLE: SummaryState = { status: 'idle' }

export function useSummary(rules: PlanRules | null, log: MealLog): SharedSummary {
  const [held, setHeld] = useState<Held>({ rules: null, log: null, state: IDLE })
  // The same as of the last call, for `need`, which runs before a render shows the new one.
  const current = useRef<Held>({ rules: null, log: null, state: IDLE })
  const request = useRef<AbortController | null>(null)

  const retry = useCallback((): void => {
    if (rules === null || log.entries.length === 0) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const settle = (state: SummaryState): void => {
      current.current = { rules, log, state }
      setHeld(current.current)
    }
    settle({ status: 'loading' })
    summarise(rules, log, { signal: controller.signal }).then(
      (summary) => {
        if (!controller.signal.aborted) settle({ status: 'ready', summary })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) settle({ status: 'failed', error })
      },
    )
  }, [rules, log])

  const need = useCallback((): void => {
    const { rules: heldRules, log: heldLog, state } = current.current
    const fresh = heldRules === rules && heldLog === log
    if (!fresh || state.status === 'idle' || state.status === 'failed') retry()
  }, [rules, log, retry])

  // A call stopped as the page goes leaves nothing loaded, so a page that comes back (React does
  // this once in development) asks again.
  useEffect(
    () => () => {
      request.current?.abort()
      if (current.current.state.status === 'loading') {
        current.current = { ...current.current, state: IDLE }
      }
    },
    [],
  )

  const state = held.rules === rules && held.log === log ? held.state : IDLE
  return useMemo(() => ({ state, need, retry }), [state, need, retry])
}
