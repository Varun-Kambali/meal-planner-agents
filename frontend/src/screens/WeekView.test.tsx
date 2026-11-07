import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'

import { sampleState } from '../test/helpers'
import { daySummary, weekSummary } from '../test/tracking'
import type { SharedSummary, SummaryState } from '../useSummary'
import { WeekView } from './WeekView'

function summary(state: SummaryState): SharedSummary {
  return { state, need: vi.fn<() => void>(), retry: vi.fn<() => void>() }
}

describe('the summary of the log', () => {
  it("shows the server's share eaten as planned and the days that met every target", () => {
    const days = [
      daySummary(),
      daySummary({ date: '2026-10-27', day_type: 'rest', failures: [], unsettled: [] }),
      daySummary({ date: '2026-10-29', day_type: 'rest', failures: [] }),
    ]
    const ready = summary({
      status: 'ready',
      summary: weekSummary({
        days,
        as_planned_share: 0.4643,
        planned_meals: 16,
        days_on_target: 1,
        days_unsettled: 1,
      }),
    })

    render(<WeekView state={sampleState()} summary={ready} />)

    expect(ready.need).toHaveBeenCalledTimes(1)
    const main = document.body
    expect(main).toHaveTextContent('The log runs from Monday 26 October to Thursday 29 October.')
    expect(main).toHaveTextContent('Eaten as planned46% of the 16 planned meals')
    expect(main).toHaveTextContent(
      "Days on target1 of the 3 days logged; for 1 more, a food with no value means it can't be told",
    )
    const table = screen.getByRole('table', { name: 'Days logged' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual([
      'Monday 26 OctoberTraining dayMisses',
      'Tuesday 27 OctoberRest dayMeets',
      "Thursday 29 OctoberRest dayCan't tell",
    ])
    expect(within(rows[1]!).getByRole('link', { name: 'Tuesday 27 October' })).toHaveAttribute(
      'href',
      '#/summary/2026-10-27',
    )
  })

  it('says why the log could not be worked out, with a way to try again', async () => {
    const failed = summary({
      status: 'failed',
      error: new ApiError(429, 'too many requests, try again later', [], null, 30),
    })
    render(<WeekView state={sampleState()} summary={failed} />)

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(
      'The log could not be worked out. The API has had too many requests. Try again in 30 seconds.',
    )
    await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(failed.retry).toHaveBeenCalledTimes(1)
  })

  it('asks for nothing while nothing is logged', () => {
    const idle = summary({ status: 'idle' })
    render(
      <WeekView state={{ ...sampleState(), log: { version: 1, entries: [] } }} summary={idle} />,
    )

    expect(screen.getByText(/Nothing is logged yet/)).toBeInTheDocument()
    expect(idle.need).not.toHaveBeenCalled()
  })
})
