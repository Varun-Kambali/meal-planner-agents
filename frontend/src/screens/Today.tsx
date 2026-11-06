// The log for one date: each planned meal of the plan, logged with one tap as planned, a
// different amount (half, one and a half, or grams of the whole meal), swapped or skipped. A
// date's planned meals are the saved week's recipes for its weekday. The log keeps foods and
// amounts only; the summary works the numbers out again (ADR 0007).

import { useEffect, useId, useRef, useState, type FormEvent, type RefObject } from 'react'

import { addDays, dateWords, weekdayOf } from '../dates'
import { saveFile, today as fileDate } from '../download'
import {
  MAX_MEAL_GRAMS,
  entryAt,
  followable,
  recipeGrams,
  recipeItems,
  spanOf,
  tooLong,
  withEntry,
  type MealStatus,
  type Span,
} from '../mealLog'
import { dayHref, logHref, recipeHref } from '../route'
import type { SaveOutcome } from '../session'
import { ValidationFailed, exportFile, type State } from '../storage'
import type { CheckedRecipe, LogEntry, LogItem, Slot } from '../types'
import { LOG_DAYS } from '../validate'
import { MEAL_STATUSES, messageOf, shown } from '../words'
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

// A meal the log could not take without running over a week, held until the person decides.
interface Waiting {
  entry: LogEntry
  span: Span
  exported: string | null
  problem: string | null
}

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

type Portion = 'half' | 'more' | 'grams'

const PORTIONS: [Portion, string][] = [
  ['half', 'Half'],
  ['more', 'One and a half'],
  ['grams', 'A weight in grams'],
]

// How much of the recipe was eaten: every ingredient is scaled by the same factor, half, one and
// a half, or the grams of the whole meal eaten over the recipe's own weight.
function AmountPanel({
  recipe,
  onSave,
  onCancel,
}: {
  recipe: CheckedRecipe
  onSave: (items: LogItem[]) => Promise<boolean>
  onCancel: () => void
}) {
  const [portion, setPortion] = useState<Portion | null>(null)
  const [grams, setGrams] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const name = useId()
  const id = useId()
  const first = useRef<HTMLInputElement>(null)
  const gramsInput = useRef<HTMLInputElement>(null)
  useEffect(() => first.current?.focus(), [])
  const weight = recipeGrams(recipe)

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    if (saving) return
    let factor: number
    if (portion === null) {
      setProblem('Choose how much of the recipe you ate.')
      first.current?.focus()
      return
    }
    if (portion === 'grams') {
      const value = Number(grams.trim())
      if (grams.trim() === '' || !Number.isFinite(value) || value <= 0 || value > MAX_MEAL_GRAMS) {
        setProblem(`Give the grams as a number above 0 and at most ${MAX_MEAL_GRAMS}.`)
        gramsInput.current?.focus()
        return
      }
      factor = value / weight
    } else factor = portion === 'half' ? 0.5 : 1.5
    setProblem(null)
    setSaving(true)
    const done = await onSave(recipeItems(recipe, factor))
    if (!done) setSaving(false)
  }

  return (
    <form className="panel" noValidate onSubmit={(event) => void submit(event)}>
      <fieldset className="choices">
        <legend>How much of the recipe did you eat?</legend>
        <div className="choice-list">
          {PORTIONS.map(([value, label], index) => (
            <label key={value} className="choice">
              <input
                type="radio"
                name={name}
                value={value}
                ref={index === 0 ? first : undefined}
                checked={portion === value}
                aria-describedby={
                  problem !== null && portion === null ? `${id}-problem` : undefined
                }
                onChange={() => setPortion(value)}
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      {portion === 'grams' && (
        <div className="field">
          <label htmlFor={`${id}-grams`}>Grams of the whole meal</label>
          <p id={`${id}-hint`} className="hint">
            The recipe as written weighs {shown(weight)} g. Every ingredient is scaled to match.
          </p>
          <input
            id={`${id}-grams`}
            ref={gramsInput}
            className="input"
            inputMode="decimal"
            autoComplete="off"
            value={grams}
            aria-invalid={problem === null ? undefined : true}
            aria-describedby={problem === null ? `${id}-hint` : `${id}-hint ${id}-problem`}
            onChange={(event) => setGrams(event.target.value)}
          />
        </div>
      )}
      {problem !== null && (
        <p id={`${id}-problem`} className="field-error">
          {problem}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="button-primary" aria-disabled={saving}>
          Save the amount
        </button>
        <button type="button" className="button-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

// The log would run over a week with this meal. Nothing is saved until the person has kept the
// log as a file; only then can a new log start with the meal.
function LogFull({
  waiting,
  log,
  meal,
  today,
  headingRef,
  onExport,
  onStart,
  onCancel,
}: {
  waiting: Waiting
  log: LogEntry[]
  meal: string
  today: string
  headingRef: RefObject<HTMLHeadingElement | null>
  onExport: () => void
  onStart: () => void
  onCancel: () => void
}) {
  const headingId = useId()
  const now = spanOf(log)
  const exported = waiting.exported !== null
  return (
    <section className="notice" aria-labelledby={headingId}>
      <h2 id={headingId} ref={headingRef} tabIndex={-1}>
        The log is full
      </h2>
      <p>
        A log covers at most {LOG_DAYS} days in a row.
        {now !== null &&
          (now.first === now.last
            ? ` Yours has meals on ${dateWords(now.first, today)} only.`
            : ` Yours runs from ${dateWords(now.first, today)} to ${dateWords(now.last, today)}.`)}{' '}
        Logging {meal} on {dateWords(waiting.entry.date, today)} would make it {waiting.span.days}{' '}
        days, so nothing has been saved.
      </p>
      <p>Save the log as a file to keep it. Then a new log can start with this meal.</p>
      <div className="actions">
        <button
          type="button"
          className={exported ? 'button-secondary' : 'button-primary'}
          onClick={onExport}
        >
          Save the log as a file
        </button>
        <button type="button" className="button-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <output>{exported && `Saved as ${waiting.exported}.`}</output>
      {waiting.problem !== null && <p role="alert">{waiting.problem}</p>}
      {exported && (
        <div className="confirm">
          <p>
            The file keeps every meal logged so far. The new log in this browser starts with this
            meal, and the meals of the old one are in the file only.
          </p>
          <button type="button" className="button-primary" onClick={onStart}>
            Start a new log with this meal
          </button>
        </div>
      )}
    </section>
  )
}
