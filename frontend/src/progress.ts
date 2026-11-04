// Where each meal of a run is, from the stream's events, in words for the live progress list.

import type { DayKey, WeekEvent } from './api'
import { reasonWords } from './words'

// The most times one recipe is tried: one writing and three repairs (MAX_ATTEMPTS in the API).
// The words say "try 2 of 4", never a try past the last.
export const MAX_TRIES = 4

export type Step =
  | { kind: 'waiting' }
  | { kind: 'writing'; attempt: number }
  | { kind: 'checking' }
  | { kind: 'checked'; failing: number; checked: number }
  | { kind: 'repairing'; attempt: number }
  | { kind: 'retrying'; reason: string }
  | { kind: 'failed'; reason: string }

export interface MealProgress {
  slotId: string
  name: string
  step: Step
  // Its recipes have arrived once, or it could not be written: what the progress bar counts.
  written: boolean
  // The failures each recipe's last check found, by day.
  failing: Partial<Record<DayKey, number>>
}

export interface Progress {
  meals: MealProgress[]
}

export function startProgress(slots: { id: string; name: string }[]): Progress {
  return {
    meals: slots.map((slot) => ({
      slotId: slot.id,
      name: slot.name,
      step: { kind: 'waiting' },
      written: false,
      failing: {},
    })),
  }
}

function changed(meal: MealProgress, event: WeekEvent): MealProgress {
  switch (event.kind) {
    case 'slot_started':
      return { ...meal, step: { kind: 'writing', attempt: event.attempt } }
    case 'slot_written':
      return { ...meal, step: { kind: 'checking' }, written: true }
    case 'recipe_checked': {
      const failing = { ...meal.failing, [event.day]: event.failures.length }
      const counts = Object.values(failing)
      return {
        ...meal,
        failing,
        step: {
          kind: 'checked',
          failing: counts.filter((count) => count > 0).length,
          checked: counts.length,
        },
      }
    }
    case 'repair_started':
      return { ...meal, step: { kind: 'repairing', attempt: event.attempt } }
    case 'slot_failed':
      return event.final
        ? { ...meal, step: { kind: 'failed', reason: event.reason }, written: true }
        : { ...meal, step: { kind: 'retrying', reason: event.reason } }
    case 'week_done':
      return meal
  }
}

export function advance(progress: Progress, event: WeekEvent): Progress {
  if (event.kind === 'week_done') return progress
  return {
    meals: progress.meals.map((meal) =>
      meal.slotId === event.slot_id ? changed(meal, event) : meal,
    ),
  }
}

function recipes(count: number): string {
  return count === 1 ? '1 recipe' : `${count} recipes`
}

export function stepWords(step: Step): string {
  switch (step.kind) {
    case 'waiting':
      return 'Waiting to start'
    case 'writing':
      return step.attempt === 1 ? 'Writing' : `Writing again, try ${step.attempt} of ${MAX_TRIES}`
    case 'checking':
      return 'Checking'
    case 'checked':
      return step.failing === 0
        ? `Checked: ${step.checked === 1 ? 'the recipe passes' : `all ${step.checked} recipes pass`}`
        : `Checked: ${recipes(step.failing)} of ${step.checked} miss a check`
    case 'repairing':
      return `Repairing, try ${step.attempt} of ${MAX_TRIES}`
    case 'retrying':
      return `The reply could not be used (${reasonWords(step.reason)}); trying again`
    case 'failed':
      return `Could not write: ${reasonWords(step.reason)}`
  }
}
