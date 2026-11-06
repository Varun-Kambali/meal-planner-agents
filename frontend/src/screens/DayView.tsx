// One date of the log against the targets of its day type: every nutrient the plan targets, eaten
// against its target, each meal's glycemic load when every food in it has one, and the checks with
// their reasons. Every number comes from the summary the server works out (POST /v1/summary),
// which the shell holds (useSummary); whether a target is a floor or a cap is read from the rules,
// since the summary gives the value only.

import { useEffect, useId } from 'react'

import { dateWords, weekdayOf } from '../dates'
import { logHref } from '../route'
import type { State } from '../storage'
import type { DaySummary, DayType, LogEntry, PlanRules } from '../types'
import type { SharedSummary } from '../useSummary'
import { MEAL_STATUSES, NUTRIENTS, shown, type NutrientKey } from '../words'
import { DayDetail, RequestProblem, Status } from './parts'

export interface DayViewProps {
  state: State
  date: string
  // The summary the shell holds for the plan and the log.
  summary: SharedSummary
}

// Carbohydrate and fat are reported against their targets and never make a day miss.
const SOFT: readonly string[] = ['carbs_available_g', 'fat_g']

function wordsOf(key: string): { name: string; unit: string } {
  return key in NUTRIENTS ? NUTRIENTS[key as NutrientKey] : { name: key, unit: '' }
}

function amount(value: number, unit: string): string {
  return unit === '' ? shown(value) : `${shown(value)} ${unit}`
}

// A target as the day type holds it: a band for calories, a floor or a cap for the rest.
function targetWords(key: string, value: number, dayType: DayType | undefined): string {
  const { unit } = wordsOf(key)
  if (key === 'kcal') return amount(value, unit)
  if (SOFT.includes(key)) return `about ${amount(value, unit)}`
  if (key === 'protein_g' || key === 'fibre_g') return `at least ${amount(value, unit)}`
  if (key === 'sat_fat_g') return `at most ${amount(value, unit)}`
  const kind = dayType?.extra.find((target) => target.nutrient === key)?.kind
  if (kind === 'min') return `at least ${amount(value, unit)}`
  if (kind === 'max') return `at most ${amount(value, unit)}`
  return amount(value, unit)
}

function checked(day: DaySummary, key: string): 'fail' | 'unsettled' | 'ok' {
  if (day.failures.some((failure) => failure.check === `day_${key}`)) return 'fail'
  if (day.unsettled.some((check) => check.check === `day_${key}`)) return 'unsettled'
  return 'ok'
}

// Eaten so far: "not known" when no food has a value, and "at least" where a food lacks one, as
// the summary shows by leaving the check unsettled or carbohydrate and fat out of `soft`.
function eatenWords(day: DaySummary, key: string): string {
  const value = day.totals[key] ?? null
  if (value === null) return 'not known'
  const lower = SOFT.includes(key) ? day.soft[key] === undefined : checked(day, key) === 'unsettled'
  return `${lower ? 'at least ' : ''}${amount(value, wordsOf(key).unit)}`
}

function softWords(fraction: number | undefined): string {
  if (fraction === undefined) return 'Not fully known'
  const percent = Math.abs(fraction) * 100
  if (percent < 0.5) return 'On the target'
  return `${shown(percent)}% ${fraction > 0 ? 'over' : 'under'}`
}

function dayTypeOf(rules: PlanRules, id: string): DayType | undefined {
  return rules.day_types.find((dayType) => dayType.id === id)
}

export function DayView({ state, date, summary }: DayViewProps) {
  const { rules, log } = state
  const entries = log.entries.filter((entry) => entry.date === date)
  const wanted = rules !== null && entries.length > 0
  const { need } = summary
  useEffect(() => {
    if (wanted) need()
  }, [wanted, need])

  const title = (
    <h1 tabIndex={-1} data-screen-title>
      {dateWords(date)}
    </h1>
  )
  if (rules === null) {
    return (
      <>
        {title}
        <p>
          No plan is saved in this browser yet. <a href="#/plan">Fill in your plan</a> first; the
          day is held to its targets.
        </p>
      </>
    )
  }
  const dayTypeId = rules.week[weekdayOf(date)]
  const links = (
    <p className="day-links">
      <a href={logHref(date)}>Log the meals of this day</a>
      <a href="#/summary">Back to the summary</a>
    </p>
  )
  return (
    <>
      {title}
      <p className="meta">{dayTypeOf(rules, dayTypeId)?.name ?? dayTypeId}</p>
      {links}
      {entries.length === 0 ? (
        <p>Nothing is logged for this day.</p>
      ) : (
        <DayBody date={date} entries={entries} rules={rules} summary={summary} />
      )}
    </>
  )
}

function DayBody({
  date,
  entries,
  rules,
  summary,
}: {
  date: string
  entries: LogEntry[]
  rules: PlanRules
  summary: SharedSummary
}) {
  const { state } = summary
  if (state.status === 'failed') {
    return (
      <RequestProblem
        what="The day could not be worked out."
        error={state.error}
        onRetry={summary.retry}
      />
    )
  }
  if (state.status !== 'ready') {
    return <output>Working out the day with nutrition-mcp.</output>
  }
  const day = state.summary.days.find((d) => d.date === date)
  if (day === undefined) return <p>Nothing is logged for this day.</p>
  return <Day day={day} entries={entries} rules={rules} />
}

function Day({ day, entries, rules }: { day: DaySummary; entries: LogEntry[]; rules: PlanRules }) {
  const mealsId = useId()
  const dayType = dayTypeOf(rules, day.day_type)
  return (
    <>
      <section className="group" aria-label="Checks">
        <DayDetail report={day} />
      </section>
      <div className="table-scroll">
        <table className="numbers day-table">
          <caption>Eaten against target</caption>
          <thead>
            <tr>
              <th scope="col">Nutrient</th>
              <th scope="col" className="num">
                Eaten
              </th>
              <th scope="col" className="num">
                Target
              </th>
              <th scope="col">Checks</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(day.targets).map(([key, value]) =>
              value === undefined ? null : (
                <tr key={key}>
                  <th scope="row">{wordsOf(key).name}</th>
                  <td className="num">{eatenWords(day, key)}</td>
                  <td className="num">{targetWords(key, value, dayType)}</td>
                  <td>
                    {SOFT.includes(key) ? (
                      softWords(day.soft[key])
                    ) : (
                      <Status verdict={checked(day, key)} />
                    )}
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
      <p className="meta">
        Carbohydrate and fat are shown against their targets and never make a day miss.
      </p>
      <h2 id={mealsId}>Meals</h2>
      <ul className="day-meals" aria-labelledby={mealsId}>
        {rules.slots.map((slot) => (
          <MealLine
            key={slot.id}
            name={slot.name}
            entry={entries.find((entry) => entry.slot_id === slot.id)}
            day={day}
          />
        ))}
      </ul>
    </>
  )
}

function MealLine({
  name,
  entry,
  day,
}: {
  name: string
  entry: LogEntry | undefined
  day: DaySummary
}) {
  const nameId = useId()
  // The summary has a glycemic load for each meal that was eaten, and none for one skipped or not
  // logged; how the meal went is the log's.
  const load = entry === undefined ? undefined : day.glycemic_load[entry.slot_id]
  const lacking = entry === undefined ? 0 : (day.gl_missing[entry.slot_id] ?? 0)
  return (
    <li aria-labelledby={nameId}>
      <p>
        <strong id={nameId}>{name}</strong>:{' '}
        {entry === undefined ? 'not logged' : MEAL_STATUSES[entry.status]}
      </p>
      {load === null && (
        <p>
          Glycemic load: not known, {lacking} {lacking === 1 ? 'item has' : 'items have'} no value
        </p>
      )}
      {load !== null && load !== undefined && (
        <p className="numbers">Glycemic load: {shown(load)}</p>
      )}
    </li>
  )
}
