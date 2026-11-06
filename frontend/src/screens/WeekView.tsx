// The log so far: the share of the planned meals eaten as planned, and the days that met every
// target, each day linking to its own view. Every number is the server's, from the summary the
// shell holds (useSummary); a slot with no entry between the log's first and last dates counts as
// not eaten as planned, as the summary counts it.

import { useEffect } from 'react'

import { dateWords } from '../dates'
import { dayHref } from '../route'
import type { State } from '../storage'
import type { PlanRules, WeekSummary } from '../types'
import type { SharedSummary } from '../useSummary'
import { shown } from '../words'
import { DayWord, RequestProblem } from './parts'

export interface WeekViewProps {
  state: State
  // The summary the shell holds for the plan and the log.
  summary: SharedSummary
}

export function WeekView({ state, summary }: WeekViewProps) {
  const { rules, log } = state
  const wanted = rules !== null && log.entries.length > 0
  const { need } = summary
  useEffect(() => {
    if (wanted) need()
  }, [wanted, need])

  return (
    <>
      <h1 tabIndex={-1} data-screen-title>
        Summary
      </h1>
      {rules === null ? (
        <p>
          No plan is saved in this browser yet. <a href="#/plan">Fill in your plan</a> first; meals
          are logged against it.
        </p>
      ) : log.entries.length === 0 ? (
        <p>
          Nothing is logged yet. <a href="#/log">Log today&apos;s meals</a> and they are added up
          here against the plan.
        </p>
      ) : summary.state.status === 'failed' ? (
        <RequestProblem
          what="The log could not be worked out."
          error={summary.state.error}
          onRetry={summary.retry}
        />
      ) : summary.state.status === 'ready' ? (
        <Summary rules={rules} summary={summary.state.summary} />
      ) : (
        <output>Working out the log with nutrition-mcp.</output>
      )}
    </>
  )
}

function Summary({ rules, summary }: { rules: PlanRules; summary: WeekSummary }) {
  const first = summary.days[0]
  const last = summary.days.at(-1)
  const logged = summary.days.length
  return (
    <>
      {first !== undefined && last !== undefined && (
        <p>
          The log runs from {dateWords(first.date)} to {dateWords(last.date)}. Each meal is worked
          out again by nutrition-mcp from the foods logged.
        </p>
      )}
      <dl className="facts summary-facts">
        <div>
          <dt>Eaten as planned</dt>
          <dd className="numbers">
            {shown(summary.as_planned_share * 100)}% of the {summary.planned_meals} planned{' '}
            {summary.planned_meals === 1 ? 'meal' : 'meals'}
          </dd>
        </div>
        <div>
          <dt>Days on target</dt>
          <dd className="numbers">
            {summary.days_on_target} of the {logged} {logged === 1 ? 'day' : 'days'} logged
            {summary.days_unsettled > 0 &&
              `; for ${summary.days_unsettled} more, a food with no value means it can't be told`}
          </dd>
        </div>
      </dl>
      <div className="table-scroll">
        <table className="summary-days">
          <caption>Days logged</caption>
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Day type</th>
              <th scope="col">Targets</th>
            </tr>
          </thead>
          <tbody>
            {summary.days.map((day) => (
              <tr key={day.date}>
                <th scope="row">
                  <a href={dayHref(day.date)}>{dateWords(day.date)}</a>
                </th>
                <td>{rules.day_types.find((d) => d.id === day.day_type)?.name ?? day.day_type}</td>
                <td>
                  <DayWord report={day} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
