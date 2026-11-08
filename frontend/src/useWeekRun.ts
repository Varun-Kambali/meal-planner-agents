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
import { fromServer, sameRules, type FormError } from './planForm'
import { advance, startProgress, type Progress } from './progress'
import type { Save } from './session'
import { ValidationFailed, type State } from './storage'
import type { WeekResult } from './types'
import { isAbort } from './useCalls'
import { DAY_NAMES, messageOf, reasonWords, refusalWords } from './words'

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
    return { kind: 'failed', run, message: refusalWords(error) }
  }
  if (error instanceof StreamFailed) {
    const what = run.what === 'week' ? 'the week' : 'the recipe'
    return {
      kind: 'failed',
      run,
      message: `The run stopped before ${what} was done: ${error.message}.`,
    }
  }
  const message = messageOf(error)
  return { kind: 'failed', run, message: `Something went wrong: ${message}.` }
}

export function useWeekRun(
  state: State | null,
  save: Save,
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
          // Every meal unwritten (the model's rate limit, say): the stream still ends in
          // week_done, and saving it would replace the week the person has with an empty one.
          if (week.recipes.length === 0) {
            const first = week.unwritten[0]?.[2]
            const why = first === undefined ? '' : `: ${reasonWords(first)}`
            setOutcome({
              kind: 'failed',
              run: what,
              message: `No recipe could be written${why}.`,
            })
            return
          }
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
        // The plan, or the week a replacement was written into, may have been replaced since the
        // run began. A week written for other rules is not saved over them.
        const unchanged = (s: State): boolean =>
          sameRules(s.rules, rules) && (what.what === 'week' || s.week === start.week)
        const planChanged: Outcome = {
          kind: 'failed',
          run: what,
          message: `The plan changed while ${
            what.what === 'week'
              ? 'the week was being written, so this week'
              : 'the recipe was being written, so it'
          } was not kept.`,
        }
        if (!unchanged(latest.current ?? start)) {
          setOutcome(planChanged)
          return
        }
        // The save is applied when its turn comes, to the state then, so a meal saved meanwhile
        // is kept. The check runs again on that state.
        const applied = { kept: true }
        const saved = await save((current) => {
          applied.kept = unchanged(current)
          return applied.kept ? { ...current, week } : current
        })
        if (!applied.kept) {
          setOutcome(planChanged)
          return
        }
        // A save the browser could not make (StorageUnavailable, say) still keeps the week on the
        // page, and the shell says it was not saved. One that would not load again keeps nothing.
        if (!saved.ok && saved.error instanceof ValidationFailed) {
          const noun = what.what === 'week' ? 'week' : 'recipe'
          setOutcome({
            kind: 'failed',
            run: what,
            message: `The ${noun} could not be saved: ${saved.error.message}.`,
          })
          return
        }
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
