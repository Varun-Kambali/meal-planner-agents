// The week: a recipe for each day and meal, each with what its checks found, written as a stream
// with each meal's step on show. A recipe that still misses a check says what it misses; a meal
// that could not be written says why. One recipe can be written again on its own.

import { useEffect, useId, useRef, useState } from 'react'

import type { DayKey } from '../api'
import { labelOf } from '../planForm'
import { stepWords, type Progress } from '../progress'
import { recipeHref } from '../route'
import type { State } from '../storage'
import type { CheckedRecipe, DayReport, PlanRules, Slot, WeekResult } from '../types'
import type { Outcome, Running, WeekRun } from '../useWeekRun'
import { useWide } from '../useWide'
import { DAYS, DAY_NAMES, reasonWords, shown } from '../words'
import { Status } from './parts'

export interface WeekProps {
  state: State
  run: WeekRun
}

function recipeAt(week: WeekResult, day: DayKey, slotId: string): CheckedRecipe | undefined {
  return week.recipes.find((recipe) => recipe.spec.day === day && recipe.spec.slot.id === slotId)
}

function unwrittenAt(week: WeekResult, day: DayKey, slotId: string): string | undefined {
  return week.unwritten.find(([d, slot]) => d === day && slot === slotId)?.[2]
}

function dayTypeName(rules: PlanRules, day: DayKey): string {
  const id = rules.week[day]
  return rules.day_types.find((dayType) => dayType.id === id)?.name ?? id
}

// "Monday's Lunch", as the buttons and headings name one meal.
function mealName(day: DayKey, slot: { name: string }): string {
  return `${DAY_NAMES[day]}'s ${slot.name}`
}

export function Week({ state, run }: WeekProps) {
  const { rules, week } = state
  const reasonId = useId()
  const writeButton = useRef<HTMLButtonElement>(null)
  const busy = run.running !== null
  // The Stop button goes once a run ends; the focus then goes back to the Write button rather
  // than to the page. Only then: opening the screen leaves the focus where it is.
  const wasBusy = useRef(false)
  useEffect(() => {
    if (wasBusy.current && !busy && document.activeElement === document.body) {
      writeButton.current?.focus()
    }
    wasBusy.current = busy
  }, [busy])
  if (rules === null) {
    return (
      <>
        <h1 tabIndex={-1} data-screen-title>
          The week
        </h1>
        <p>
          No plan is saved in this browser yet. <a href="#/plan">Fill in your plan</a> first; the
          week is written for it.
        </p>
      </>
    )
  }
  return (
    <>
      <h1 tabIndex={-1} data-screen-title>
        The week
      </h1>
      <p>Recipes for {rules.name}, one for each meal of each day, checked against the plan.</p>
      <div className="actions">
        <button
          type="button"
          className="button-primary"
          ref={writeButton}
          // aria-disabled, not disabled, so that the focus stays on the button while a run goes.
          aria-disabled={busy}
          aria-describedby={reasonId}
          onClick={() => {
            if (!busy) run.write()
          }}
        >
          {week === null ? 'Write the week' : 'Write a new week'}
        </button>
        {busy && (
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              writeButton.current?.focus()
              run.stop()
            }}
          >
            Stop
          </button>
        )}
      </div>
      <p id={reasonId} className="hint">
        {busy
          ? 'A run is going. It takes a few minutes, and you can open other screens meanwhile.'
          : week === null
            ? 'Writing takes a few minutes. Each meal is written, checked against the plan and repaired up to three times.'
            : 'A new week replaces this one once it is done. Writing takes a few minutes.'}
      </p>
      {run.running !== null && run.progress !== null && (
        <ProgressPanel running={run.running} progress={run.progress} rules={rules} />
      )}
      <output className="outcome">{statusOf(run.outcome, rules)}</output>
      {run.outcome !== null && <OutcomeNotice outcome={run.outcome} hadWeek={week !== null} />}
      {week === null ? (
        <p>No week has been written for this plan yet.</p>
      ) : (
        <WeekView rules={rules} week={week} run={run} />
      )}
    </>
  )
}

function ProgressPanel({
  running,
  progress,
  rules,
}: {
  running: Running
  progress: Progress
  rules: PlanRules
}) {
  const headingId = useId()
  const countId = useId()
  let title = 'Writing the week'
  if (running.what === 'replace') {
    const slot = rules.slots.find((s) => s.id === running.slotId)
    title = `Writing ${mealName(running.day, slot ?? { name: running.slotId })}`
  }
  const total = progress.meals.length
  const written = progress.meals.filter((meal) => meal.written).length
  return (
    <section className="progress" aria-labelledby={headingId}>
      <h2 id={headingId}>{title}</h2>
      <p id={countId} className="numbers">
        {written} of {total} {total === 1 ? 'meal' : 'meals'} written
      </p>
      <progress value={written} max={total} aria-labelledby={countId} />
      <ul aria-live="polite">
        {progress.meals.map((meal) => (
          <li key={meal.slotId}>
            {meal.name}: {stepWords(meal.step)}
          </li>
        ))}
      </ul>
    </section>
  )
}

// How a run that worked, or was stopped, ended, for the status line. It is on the page from the
// start, so that a screen reader reads out the change.
function statusOf(outcome: Outcome | null, rules: PlanRules): string {
  if (outcome === null) return ''
  const { run } = outcome
  if (outcome.kind === 'stopped') return 'Stopped. Nothing was changed.'
  if (outcome.kind !== 'written') return ''
  if (run.what === 'week') return 'The week is written.'
  const slot = rules.slots.find((s) => s.id === run.slotId) ?? { name: run.slotId }
  return `${mealName(run.day, slot)} is written again.`
}

function OutcomeNotice({ outcome, hadWeek }: { outcome: Outcome; hadWeek: boolean }) {
  const headingId = useId()
  const { run } = outcome
  const unchanged =
    run.what === 'replace'
      ? 'The recipe you had is unchanged.'
      : hadWeek
        ? 'The week you had is unchanged.'
        : 'No week was saved.'
  switch (outcome.kind) {
    case 'written':
    case 'stopped':
      return null
    case 'failed':
      return (
        <div role="alert" className="notice">
          <p>{outcome.message}</p>
          <p>{unchanged}</p>
        </div>
      )
    case 'refused':
      return (
        <section role="alert" className="notice" aria-labelledby={headingId}>
          <h2 id={headingId}>The API refused the plan</h2>
          {outcome.rules.length > 0 && (
            <>
              <ul>
                {outcome.rules.map((problem, index) => {
                  const where = labelOf(problem.path)
                  return (
                    <li key={`${problem.path}-${index}`}>
                      {where === '' ? problem.message : `${where}: ${problem.message}`}
                    </li>
                  )
                })}
              </ul>
              <p>
                <a href="#/plan">Fix them in your plan</a>, save it, then write the week again.
              </p>
            </>
          )}
          {outcome.other.length > 0 && (
            <ul>
              {outcome.other.map((message, index) => (
                <li key={index}>{message}</li>
              ))}
            </ul>
          )}
          <p>{unchanged}</p>
        </section>
      )
  }
}

function WeekView({ rules, week, run }: { rules: PlanRules; week: WeekResult; run: WeekRun }) {
  const wide = useWide()
  const total = DAYS.length * rules.slots.length
  const passing = week.recipes.filter((recipe) => recipe.failures.length === 0).length
  const unwritten = week.unwritten.length
  return (
    <>
      <p>
        {passing} of {total} recipes meet every check
        {unwritten > 0 && `; ${unwritten} could not be written`}.{' '}
        <a href="#/recipes">Print every recipe</a>
      </p>
      {wide ? (
        <WeekTable rules={rules} week={week} run={run} />
      ) : (
        <DayByDay rules={rules} week={week} run={run} />
      )}
      {wide && <DayChecks rules={rules} week={week} />}
      {week.no_waste.length > 0 && (
        <section aria-labelledby="no-waste-heading" className="group">
          <h2 id="no-waste-heading">Food that may go to waste</h2>
          <ul>
            {week.no_waste.map((row) => (
              <li key={`${row.ingredient}-${row.window_start}`}>
                <Status verdict="warn" word="Warning:">
                  {row.failure.message}
                </Status>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="meta">
        Written with {week.calls} model {week.calls === 1 ? 'call' : 'calls'} (
        {shown(week.input_tokens)} tokens in, {shown(week.output_tokens)} out)
        {week.cost_usd !== null &&
          `, about ${week.cost_usd.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 3 })}`}
        .
      </p>
    </>
  )
}

function WeekTable({ rules, week, run }: { rules: PlanRules; week: WeekResult; run: WeekRun }) {
  return (
    <div className="table-scroll">
      <table className="week-table">
        <caption>Recipes for each day and meal</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            {rules.slots.map((slot) => (
              <th scope="col" key={slot.id}>
                {slot.name} <span className="meta">{slot.eat_at.slice(0, 5)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {DAYS.map((day) => (
            <tr key={day}>
              <th scope="row">
                {DAY_NAMES[day]}
                <span className="meta block">{dayTypeName(rules, day)}</span>
                <span className="block">
                  <DayWord report={week.days.find((report) => report.day === day)} />
                </span>
              </th>
              {rules.slots.map((slot) => (
                <td key={slot.id}>
                  <Meal day={day} slot={slot} week={week} run={run} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Below 1024px: one day at a time, with the day before and after.
function DayByDay({ rules, week, run }: { rules: PlanRules; week: WeekResult; run: WeekRun }) {
  const [day, setDay] = useState<DayKey>(() => DAYS[(new Date().getDay() + 6) % 7]!)
  const headingId = useId()
  const index = DAYS.indexOf(day)
  const before = DAYS[index - 1]
  const after = DAYS[index + 1]
  return (
    <section aria-labelledby={headingId} className="day">
      <h2 id={headingId}>{DAY_NAMES[day]}</h2>
      <p className="meta">{dayTypeName(rules, day)}</p>
      <DayDetail report={week.days.find((report) => report.day === day)} />
      <div className="actions">
        {before !== undefined && (
          <button type="button" className="button-secondary" onClick={() => setDay(before)}>
            Previous day: {DAY_NAMES[before]}
          </button>
        )}
        {after !== undefined && (
          <button type="button" className="button-secondary" onClick={() => setDay(after)}>
            Next day: {DAY_NAMES[after]}
          </button>
        )}
      </div>
      {rules.slots.map((slot) => (
        <div key={slot.id} className="meal">
          <h3>
            {slot.name} <span className="meta">{slot.eat_at.slice(0, 5)}</span>
          </h3>
          <Meal day={day} slot={slot} week={week} run={run} />
        </div>
      ))}
    </section>
  )
}

// A day's checks in one word or two, for the table's row header.
function DayWord({ report }: { report: DayReport | undefined }) {
  if (report === undefined) return <span className="meta">Not checked</span>
  if (report.failures.length > 0) return <Status verdict="fail" />
  if (report.unsettled.length > 0) return <Status verdict="unsettled" />
  return <Status verdict="ok" />
}

function DayDetail({ report }: { report: DayReport | undefined }) {
  if (report === undefined) return <p className="meta">This day has not been checked.</p>
  if (report.failures.length === 0 && report.unsettled.length === 0) {
    return (
      <p>
        <Status verdict="ok">every day target</Status>
      </p>
    )
  }
  return (
    <>
      {report.failures.length > 0 && (
        <>
          <p>
            <Status verdict="fail" word="Misses:" />
          </p>
          <ul className="reasons">
            {report.failures.map((failure, index) => (
              <li key={index}>{failure.message}</li>
            ))}
          </ul>
        </>
      )}
      {report.unsettled.length > 0 && (
        <>
          <p>
            <Status verdict="unsettled" word="Can't tell:" />
          </p>
          <ul className="reasons">
            {report.unsettled.map((check, index) => (
              <li key={index}>{check.message}</li>
            ))}
          </ul>
        </>
      )}
    </>
  )
}

function DayChecks({ rules, week }: { rules: PlanRules; week: WeekResult }) {
  return (
    <section aria-labelledby="day-checks-heading" className="group">
      <h2 id="day-checks-heading">Day targets</h2>
      <p>Each day's recipes together, against the targets of its day type.</p>
      <dl className="day-checks">
        {DAYS.map((day) => (
          <div key={day}>
            <dt>
              {DAY_NAMES[day]} <span className="meta">{dayTypeName(rules, day)}</span>
            </dt>
            <dd>
              <DayDetail report={week.days.find((report) => report.day === day)} />
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function Meal({
  day,
  slot,
  week,
  run,
}: {
  day: DayKey
  slot: Slot
  week: WeekResult
  run: WeekRun
}) {
  const recipe = recipeAt(week, day, slot.id)
  const reason = unwrittenAt(week, day, slot.id)
  const busy = run.running !== null
  const replacing =
    run.running?.what === 'replace' && run.running.day === day && run.running.slotId === slot.id
  const name = mealName(day, slot)
  return (
    <div className="recipe-cell">
      {recipe !== undefined && (
        <>
          <a href={recipeHref(day, slot.id)} className="recipe-link">
            {recipe.draft.title}
          </a>
          <p className="meta">
            {recipe.draft.cuisine}
            {recipe.nutrition?.total.kcal != null && `, ${shown(recipe.nutrition.total.kcal)} kcal`}
          </p>
          {recipe.failures.length === 0 ? (
            <p>
              <Status verdict="ok">every check</Status>
            </p>
          ) : (
            <>
              <p>
                <Status verdict="fail" word="Misses:" />
              </p>
              <ul className="reasons">
                {recipe.failures.map((failure, index) => (
                  <li key={index}>{failure.message}</li>
                ))}
              </ul>
            </>
          )}
          {recipe.portion_scale !== 1 && (
            <p className="meta">
              Amounts scaled by{' '}
              {recipe.portion_scale.toLocaleString(undefined, { maximumFractionDigits: 2 })} to fit
              the meal&apos;s calories
            </p>
          )}
          {recipe.attempts > 1 && <p className="meta">Tried {recipe.attempts} times</p>}
        </>
      )}
      {recipe === undefined && reason !== undefined && (
        <p>
          <Status verdict="fail" word="Could not write:">
            {reasonWords(reason)}
          </Status>
        </p>
      )}
      {recipe === undefined && reason === undefined && <p className="meta">Not written</p>}
      {replacing && <p className="meta">Being written again</p>}
      <button
        type="button"
        className="button-text"
        aria-disabled={busy}
        aria-label={recipe === undefined ? `Write again: ${name}` : `Replace ${name}`}
        onClick={() => {
          if (!busy) run.replace(day, slot.id)
        }}
      >
        {recipe === undefined ? 'Write again' : 'Replace'}
      </button>
    </div>
  )
}
