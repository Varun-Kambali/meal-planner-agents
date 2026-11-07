// The meal log as the log screen changes it, held to the server's rules for a log
// (src/meal_planner/summary.py): one entry for each date and slot, at most seven consecutive days,
// no foods for a skipped meal and at least one for any other. Amounts are scaled here; no
// nutrition number is ever worked out here, since the summary works every meal out again.

import { daysFrom } from './dates'
import type { CheckedRecipe, LogEntry, LogItem, MealLog } from './types'
import { LOG_DAYS } from './validate'

export type MealStatus = LogEntry['status']

// The most grams of a whole meal "a different amount" takes: nutrition-mcp's limit for one food,
// so no ingredient of the scaled recipe can pass it.
export const MAX_MEAL_GRAMS = 5000

export function entryAt(log: MealLog, date: string, slotId: string): LogEntry | undefined {
  return log.entries.find((entry) => entry.date === date && entry.slot_id === slotId)
}

// The log with `entry` in place of the one for its date and slot, or after the others when there
// is none. The person chose the new status for that meal, so the old entry is replaced on purpose.
export function withEntry(log: MealLog, entry: LogEntry): MealLog {
  const at = log.entries.findIndex((e) => e.date === entry.date && e.slot_id === entry.slot_id)
  const entries = at === -1 ? [...log.entries, entry] : log.entries.with(at, entry)
  return { ...log, entries }
}

export interface Span {
  first: string
  last: string
  days: number
}

// The dates entries run over, from the first to the last; null for none.
export function spanOf(entries: LogEntry[]): Span | null {
  if (entries.length === 0) return null
  const dates = entries.map((entry) => entry.date).sort()
  const first = dates[0]!
  const last = dates[dates.length - 1]!
  return { first, last, days: daysFrom(first, last) }
}

// The span the log would have with `entry`, when that is longer than a log may be; else null.
export function tooLong(log: MealLog, entry: LogEntry): Span | null {
  const span = spanOf(withEntry(log, entry).entries)
  return span !== null && span.days > LOG_DAYS ? span : null
}

// A recipe a meal can be logged against as planned or in a different amount: one that was written
// and that nutrition-mcp worked out, whether or not it met its checks. One with no numbers has a
// food nutrition-mcp refused, which the summary would refuse too. Any meal can still be logged as
// swapped or skipped.
export function followable(recipe: CheckedRecipe | undefined): recipe is CheckedRecipe {
  return recipe !== undefined && recipe.nutrition !== null
}

// The weight of the recipe as written, which "grams of the whole meal" is measured against.
export function recipeGrams(recipe: CheckedRecipe): number {
  return recipe.draft.ingredients.reduce((sum, ingredient) => sum + ingredient.grams, 0)
}

// The recipe's ingredients at their final grams, each times `factor`, in grams. A scaled amount is
// kept to two decimals and above 0, as a log item's amount must be.
export function recipeItems(recipe: CheckedRecipe, factor = 1): LogItem[] {
  return recipe.draft.ingredients.map((ingredient) => {
    const grams =
      factor === 1
        ? ingredient.grams
        : Math.max(0.01, Math.round(ingredient.grams * factor * 100) / 100)
    return { food_id: ingredient.id, amount: grams, unit: 'g' }
  })
}
