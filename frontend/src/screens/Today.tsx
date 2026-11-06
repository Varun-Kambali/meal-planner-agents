// The log for one date: each planned meal of the plan, logged with one tap as planned, a
// different amount (half, one and a half, or grams of the whole meal), swapped or skipped. A
// date's planned meals are the saved week's recipes for its weekday. The log keeps foods and
// amounts only; the summary works the numbers out again (ADR 0007).

import { useEffect, useId, useRef, useState } from 'react'

import { addDays, dateWords, weekdayOf } from '../dates'
import { saveFile, today as fileDate } from '../download'
import { entryAt, followable, recipeItems, tooLong, withEntry, type MealStatus } from '../mealLog'
import { dayHref, logHref, recipeHref } from '../route'
import type { SaveOutcome } from '../session'
import { ValidationFailed, exportFile, type State } from '../storage'
import type { CheckedRecipe, LogEntry, LogItem, Slot } from '../types'
import { MEAL_STATUSES, messageOf } from '../words'
import { AmountPanel } from './AmountPanel'
import { LogFull, type Waiting } from './LogFull'
import { SwapFlow } from './SwapFlow'

export interface TodayProps {
  state: State
  date: string
  // The reader's own date, so the screen can say which date is today.
  today: string
  onSave: (state: State) => Promise<SaveOutcome>
}

const BUTTONS: [MealStatus, string][] = [
  ['as_planned', 'As planned'],
  ['different_amount', 'Different amount'],
  ['swapped', 'Swapped'],
  ['skipped', 'Skipped'],
]

type Panel = { slotId: string; status: 'different_amount' | 'swapped' } | null

function logged(entry: LogEntry | undefined): string {
  if (entry === undefined) return 'Not logged yet.'
  if (entry.status === 'swapped') {
    const count = entry.items.length
    return `Logged: swapped, ${count} ${count === 1 ? 'food' : 'foods'}.`
  }
  return `Logged: ${MEAL_STATUSES[entry.status]}.`
}

export function Today({ state, date, today, onSave }: TodayProps) {
  const { rules, week, log } = state
  const [panel, setPanel] = useState<Panel>(null)
  const [said, setSaid] = useState('')
  const [problem, setProblem] = useState<{ slotId: string; message: string } | null>(null)
  const [waiting, setWaiting] = useState<Waiting | null>(null)
  // The button the focus goes back to once a meal is saved or its panel closes, by its meal and
  // status; it is moved after the render that closes the panel.
  const focusTo = useRef<string | null>(null)
  const buttons = useRef(new Map<string, HTMLButtonElement>())
  const waitingHeading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    if (focusTo.current === null) return
    buttons.current.get(focusTo.current)?.focus()
    focusTo.current = null
  })
  const hasWaiting = waiting !== null
  useEffect(() => {
    if (hasWaiting) waitingHeading.current?.focus()
  }, [hasWaiting])

  const words = dateWords(date, today)
  const heading = date === today ? `Today, ${words}` : words
  if (rules === null) {
    return (
      <>
        <h1 tabIndex={-1} data-screen-title>
          {heading}
        </h1>
        <p>
          No plan is saved in this browser yet. <a href="#/plan">Fill in your plan</a> first; meals
          are logged against it.
        </p>
      </>
    )
  }
  const day = weekdayOf(date)
  const dayTypeId = rules.week[day]
  const dayType = rules.day_types.find((d) => d.id === dayTypeId)?.name ?? dayTypeId
  const slotName = (slotId: string): string =>
    rules.slots.find((slot) => slot.id === slotId)?.name ?? slotId

  // Saves the state; true once it is saved or kept on the page. One the browser refuses because
  // it would not load again is said to under the meal, and nothing changes.
  async function commit(next: State, entry: LogEntry): Promise<boolean> {
    setProblem(null)
    const outcome = await onSave(next)
    if (!outcome.ok && outcome.error instanceof ValidationFailed) {
      setProblem({ slotId: entry.slot_id, message: `Not saved: ${outcome.error.message}.` })
      return false
    }
    const kept = outcome.ok ? '' : ' It is kept on this page, but not saved in this browser.'
    // "as planned" already starts with "as".
    const how = entry.status === 'as_planned' ? 'as planned' : `as ${MEAL_STATUSES[entry.status]}`
    setSaid(`${slotName(entry.slot_id)} logged ${how}.${kept}`)
    setPanel(null)
    focusTo.current = `${entry.slot_id} ${entry.status}`
    return true
  }

  // A log covers at most a week. A meal that would make it longer is held, and nothing is saved,
  // until the person has saved the log as a file and starts a new one.
  async function logMeal(entry: LogEntry): Promise<boolean> {
    const span = tooLong(log, entry)
    if (span !== null) {
      setWaiting({ entry, span, exported: null, problem: null })
      return false
    }
    return commit({ ...state, log: withEntry(log, entry) }, entry)
  }

  function exportLog(): void {
    if (waiting === null) return
    try {
      const name = `meal-planner-${fileDate()}.json`
      saveFile(exportFile(state), name)
      setWaiting({ ...waiting, exported: name, problem: null })
    } catch (error) {
      setWaiting({ ...waiting, problem: `The file could not be made: ${messageOf(error)}.` })
    }
  }

  async function startNewLog(): Promise<void> {
    if (waiting === null || waiting.exported === null) return
    const done = await commit(
      { ...state, log: { version: 1, entries: [waiting.entry] } },
      waiting.entry,
    )
    if (done) setWaiting(null)
  }

  function close(slotId: string, status: MealStatus): void {
    setPanel(null)
    focusTo.current = `${slotId} ${status}`
  }

  const before = addDays(date, -1)
  const after = addDays(date, 1)
  return (
    <>
      <h1 tabIndex={-1} data-screen-title>
        {heading}
      </h1>
      <p className="meta">{dayType}</p>
      <p className="day-links">
        <a href={logHref(before)}>Previous day: {dateWords(before, today)}</a>
        <a href={logHref(after)}>Next day: {dateWords(after, today)}</a>
      </p>
      <p>
        One tap for each meal: as planned, a different amount, swapped or skipped.{' '}
        <a href={dayHref(date)}>This day against its targets</a>
      </p>
      <output className="outcome">{said}</output>
      {waiting !== null && (
        <LogFull
          waiting={waiting}
          log={log.entries}
          meal={slotName(waiting.entry.slot_id)}
          today={today}
          headingRef={waitingHeading}
          onExport={exportLog}
          onStart={() => void startNewLog()}
          onCancel={() => {
            const { slot_id, status: kind } = waiting.entry
            setWaiting(null)
            focusTo.current = `${slot_id} ${kind}`
          }}
        />
      )}
      {week === null && (
        <p>
          No week is written for this plan yet, so a meal can be logged only as swapped or skipped.{' '}
          <a href="#/week">Write the week</a>
        </p>
      )}
      {rules.slots.map((slot) => (
        <Meal
          key={slot.id}
          slot={slot}
          recipe={week?.recipes.find((r) => r.spec.day === day && r.spec.slot.id === slot.id)}
          entry={entryAt(log, date, slot.id)}
          panel={panel?.slotId === slot.id ? panel.status : null}
          problem={problem?.slotId === slot.id ? problem.message : null}
          buttonRef={(status, element) => {
            const key = `${slot.id} ${status}`
            if (element === null) buttons.current.delete(key)
            else buttons.current.set(key, element)
          }}
          onOpen={(status) => {
            setProblem(null)
            setPanel((open) =>
              open?.slotId === slot.id && open.status === status
                ? null
                : { slotId: slot.id, status },
            )
          }}
          onClose={(status) => close(slot.id, status)}
          onLog={(status, items) => logMeal({ date, slot_id: slot.id, status, items })}
        />
      ))}
    </>
  )
}

function Meal({
  slot,
  recipe,
  entry,
  panel,
  problem,
  buttonRef,
  onOpen,
  onClose,
  onLog,
}: {
  slot: Slot
  recipe: CheckedRecipe | undefined
  entry: LogEntry | undefined
  panel: 'different_amount' | 'swapped' | null
  problem: string | null
  buttonRef: (status: MealStatus, element: HTMLButtonElement | null) => void
  onOpen: (status: 'different_amount' | 'swapped') => void
  onClose: (status: MealStatus) => void
  onLog: (status: MealStatus, items: LogItem[]) => Promise<boolean>
}) {
  const headingId = useId()
  const reasonId = useId()
  const usable = followable(recipe)
  let reason: string | null = null
  if (recipe === undefined) {
    reason = 'No recipe was written for this meal, so it can be logged only as swapped or skipped.'
  } else if (!usable) {
    reason = 'This recipe misses a check, so the meal can be logged only as swapped or skipped.'
  }
  return (
    <section className="log-meal" aria-labelledby={headingId}>
      <h2 id={headingId}>
        {slot.name} <span className="meta">{slot.eat_at.slice(0, 5)}</span>
      </h2>
      {recipe !== undefined && (
        <p>
          Planned:{' '}
          <a href={recipeHref(recipe.spec.day, slot.id)} className="recipe-link">
            {recipe.draft.title}
          </a>
        </p>
      )}
      <p>{logged(entry)}</p>
      <div className="actions">
        {BUTTONS.map(([status, label]) => {
          const needsRecipe = status === 'as_planned' || status === 'different_amount'
          const off = needsRecipe && !usable
          const opens = status === 'different_amount' || status === 'swapped'
          return (
            <button
              key={status}
              type="button"
              className="button-secondary"
              ref={(element) => buttonRef(status, element)}
              aria-disabled={off ? true : undefined}
              aria-describedby={off ? reasonId : undefined}
              aria-expanded={opens ? panel === status : undefined}
              onClick={() => {
                if (off) return
                if (status === 'different_amount' || status === 'swapped') onOpen(status)
                else if (status === 'as_planned' && usable) void onLog(status, recipeItems(recipe))
                else void onLog('skipped', [])
              }}
            >
              {label}
            </button>
          )
        })}
      </div>
      {reason !== null && (
        <p id={reasonId} className="hint">
          {reason}
        </p>
      )}
      {problem !== null && (
        <p role="alert" className="field-error">
          {problem}
        </p>
      )}
      {panel === 'different_amount' && usable && (
        <AmountPanel
          recipe={recipe}
          onSave={(items) => onLog('different_amount', items)}
          onCancel={() => onClose('different_amount')}
        />
      )}
      {panel === 'swapped' && (
        <SwapFlow onSave={(items) => onLog('swapped', items)} onCancel={() => onClose('swapped')} />
      )}
    </section>
  )
}
