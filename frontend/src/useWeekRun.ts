// Writing a week, or one recipe of it again, as the stream reports it. The run belongs to the
// app, not to the week screen, so moving to another screen does not stop it; leaving the page
// does, and the server then cancels the run.

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  StreamFailed,
  replaceRecipe,
  streamWeek,
  type DayKey,
  type WeekEvent,
} from './api'
import { fromServer, type FormError } from './planForm'
import { advance, startProgress, type Progress } from './progress'
import type { SaveOutcome } from './session'
import type { State } from './storage'
import type { WeekResult } from './types'
import { DAY_NAMES, reasonWords } from './words'

export type Running = { what: 'week' } | { what: 'replace'; day: DayKey; slotId: string }

export type Outcome =
  | { kind: 'written'; run: Running }
  | { kind: 'stopped'; run: Running }
  | { kind: 'refused'; run: Running; rules: FormError[]; other: string[] }
  | { kind: 'failed'; run: Running; message: string }

export interface WeekRun {
  running: Running | null
  progress: Progress | null
  outcome: Outcome | null
  write: () => void
  replace: (day: DayKey, slotId: string) => void
  stop: () => void
}

// A replacement's week names only its own meal when it could not be written, so the meals the
// week could not write before are kept from the week it was given.
export function mergeUnwritten(
  before: WeekResult['unwritten'],
  after: WeekResult['unwritten'],
  day: DayKey,
  slotId: string,
): WeekResult['unwritten'] {
  return [...before.filter(([d, slot]) => d !== day || slot !== slotId), ...after]
}

function isAbort(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === 'AbortError'
}

function failure(error: unknown, run: Running): Outcome {
  if (isAbort(error)) return { kind: 'stopped', run }
  if (error instanceof ApiError) {
    if (error.status === 422) {
      const under = run.what === 'week' ? ['body'] : ['body', 'rules']
      const inRules = (loc: (string | number)[]): boolean =>
        under.every((part, i) => String(loc[i]) === part)
      return {
        kind: 'refused',
        run,
        rules: fromServer(error.problems, under),
        other: error.problems.filter((problem) => !inRules(problem.loc)).map((p) => p.msg),
      }
    }
    if (error.status === 429) {
      const wait = error.retryAfter ?? 60
      return {
        kind: 'failed',
        run,
        message: `The API has had too many requests. Try again in ${wait} seconds.`,
      }
    }
    if (error.status === 0)
      return {
        kind: 'failed',
        run,
        message: 'The API could not be reached; check the connection and try again.',
      }
    return { kind: 'failed', run, message: `The API said: ${error.message}.` }
  }
  if (error instanceof StreamFailed) {
    const what = run.what === 'week' ? 'the week' : 'the recipe'
    return {
      kind: 'failed',
      run,
      message: `The run stopped before ${what} was done: ${error.message}.`,
    }
  }
  const message = error instanceof Error ? error.message : String(error)
  return { kind: 'failed', run, message: `Something went wrong: ${message}.` }
}

export function useWeekRun(
  state: State | null,
  save: (state: State) => Promise<SaveOutcome>,
  onRefused: (errors: FormError[]) => void,
): WeekRun {
  const [running, setRunning] = useState<Running | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const controller = useRef<AbortController | null>(null)
  // The state as it is when the run ends, which the log may have changed since it started.
  const latest = useRef(state)
  useEffect(() => {
    latest.current = state
  }, [state])
  useEffect(() => () => controller.current?.abort(), [])

  // Never rejects: every way a run can end becomes an outcome.
  const run = useCallback(
    async (what: Running): Promise<void> => {
      const start = latest.current
      if (start === null || start.rules === null || controller.current !== null) return
      if (what.what === 'replace' && start.week === null) return
      const rules = start.rules
      const abort = new AbortController()
      controller.current = abort
      setRunning(what)
      setOutcome(null)
      const meals =
        what.what === 'week' ? rules.slots : rules.slots.filter((s) => s.id === what.slotId)
      setProgress(startProgress(meals))
      const onEvent = (event: WeekEvent): void => {
        setProgress((current) => (current === null ? current : advance(current, event)))
      }
      try {
        let week: WeekResult
        if (what.what === 'week') {
          week = await streamWeek(rules, onEvent, { signal: abort.signal })
        } else {
          const before = start.week!
          const result = await replaceRecipe(rules, before, what.day, what.slotId, onEvent, {
            signal: abort.signal,
          })
          // No new recipe: the server kept the old one, or the meal stays unwritten. Nothing
          // the person asked for changed, so nothing is saved.
          const reason = result.unwritten.find(
            ([day, slot]) => day === what.day && slot === what.slotId,
          )?.[2]
          if (reason !== undefined) {
            const slot = rules.slots.find((s) => s.id === what.slotId)
            const meal = `${DAY_NAMES[what.day]}'s ${slot?.name ?? what.slotId}`
            setOutcome({
              kind: 'failed',
              run: what,
              message: `No new recipe could be written for ${meal}: ${reasonWords(reason)}.`,
            })
            return
          }
          week = {
            ...result,
            unwritten: mergeUnwritten(before.unwritten, result.unwritten, what.day, what.slotId),
          }
        }
        await save({ ...(latest.current ?? start), rules, week })
        setOutcome({ kind: 'written', run: what })
      } catch (error) {
        const ended = failure(error, what)
        if (ended.kind === 'refused' && ended.rules.length > 0) onRefused(ended.rules)
        setOutcome(ended)
      } finally {
        controller.current = null
        setRunning(null)
        setProgress(null)
      }
    },
    [save, onRefused],
  )

  return {
    running,
    progress,
    outcome,
    write: () => void run({ what: 'week' }),
    replace: (day, slotId) => void run({ what: 'replace', day, slotId }),
    stop: () => controller.current?.abort(),
  }
}
