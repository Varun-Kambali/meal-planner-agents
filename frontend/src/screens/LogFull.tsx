// The notice the log screen (Today.tsx) shows when a meal would make the log longer than a week.

import { useId, type RefObject } from 'react'

import { dateWords } from '../dates'
import { spanOf, type Span } from '../mealLog'
import type { LogEntry } from '../types'
import { LOG_DAYS } from '../validate'

// A meal the log could not take without running over a week, held until the person decides.
export interface Waiting {
  entry: LogEntry
  span: Span
  exported: string | null
  problem: string | null
}

// The log would run over a week with this meal. Nothing is saved until the person has kept the
// log as a file; only then can a new log start with the meal.
export function LogFull({
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
    <section className="notice log-full" aria-labelledby={headingId}>
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
