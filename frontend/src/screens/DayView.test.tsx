import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api'

import { sampleState } from '../test/helpers'
import { weekSummary } from '../test/tracking'
import type { SharedSummary, SummaryState } from '../useSummary'
import { DayView } from './DayView'

function summary(state: SummaryState = { status: 'ready', summary: weekSummary() }): SharedSummary {
  return { state, need: vi.fn<() => void>(), retry: vi.fn<() => void>() }
}

describe('the day view', () => {
  it('says the glycemic load is not known, with how many items lack one', () => {
    render(<DayView state={sampleState()} date="2026-10-26" summary={summary()} />)

    const meals = screen.getByRole('list', { name: 'Meals' })
    expect(within(meals).getByRole('listitem', { name: /^Breakfast/ })).toHaveTextContent(
      'Glycemic load: not known, 2 items have no value',
    )
    expect(within(meals).getByRole('listitem', { name: /^Lunch/ })).toHaveTextContent(
      'Glycemic load: 18',
    )
    expect(within(meals).getByRole('listitem', { name: /^Evening snack/ })).toHaveTextContent(
      'Glycemic load: not known, 1 item has no value',
    )
  })
})

describe('the day against its targets', () => {
  it('holds every nutrient the plan targets to its target, in the summary words', () => {
    render(<DayView state={sampleState()} date="2026-10-26" summary={summary()} />)

    const table = screen.getByRole('table', { name: 'Eaten against target' })
    const row = (name: string): string =>
      within(table).getByRole('row', { name: new RegExp(`^${name}`) }).textContent ?? ''
    expect(row('Energy')).toBe('Energy2,410 kcal2,650 kcalMeets')
    expect(row('Protein')).toBe('Protein118 gat least 150 gMisses')
    expect(row('Saturated fat')).toBe('Saturated fat21 gat most 28 gMeets')
    expect(row('Fibre')).toBe("Fibreat least 22 gat least 35 gCan't tell")
    expect(row('Carbohydrate')).toBe('Carbohydrate320 gabout 300 g7% over')
    expect(row('Fat')).toBe('Fat70 gabout 80 g13% under')
    expect(row('Calcium')).toBe('Calcium1,040 mgat least 1,000 mgMeets')
    expect(row('Sodium')).toBe('Sodium1,900 mgat most 2,300 mgMeets')
    expect(within(table).getAllByRole('row')).toHaveLength(9)

    const checks = screen.getByRole('region', { name: 'Checks' })
    expect(checks).toHaveTextContent(/Misses:\s*protein 118 g, the day needs at least 150 g/)
    expect(checks).toHaveTextContent(/Can't tell:\s*fibre not known \(no value for moong dal\)/)
    const meals = screen.getByRole('list', { name: 'Meals' })
    expect(within(meals).getByRole('listitem', { name: /^Breakfast/ })).toHaveTextContent(
      'Breakfast: as planned',
    )
    expect(within(meals).getByRole('listitem', { name: /^Dinner/ })).toHaveTextContent(
      'Dinner: not logged',
    )
  })

  it('asks for the summary when it opens, and says why it failed with a way to try again', async () => {
    const waiting = summary({ status: 'idle' })
    const { rerender } = render(
      <DayView state={sampleState()} date="2026-10-26" summary={waiting} />,
    )
    expect(waiting.need).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('Working out the day with nutrition-mcp.')

    const failed = summary({
      status: 'failed',
      error: new ApiError(503, 'nutrition-mcp could not be reached'),
    })
    rerender(<DayView state={sampleState()} date="2026-10-26" summary={failed} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(
      'The day could not be worked out. The API said: nutrition-mcp could not be reached.',
    )
    await userEvent.setup().click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(failed.retry).toHaveBeenCalledTimes(1)
  })

  it('asks for nothing on a date with nothing logged', () => {
    const idle = summary({ status: 'idle' })
    render(<DayView state={sampleState()} date="2026-10-27" summary={idle} />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Tuesday 27 October')
    expect(screen.getByText('Nothing is logged for this day.')).toBeInTheDocument()
    expect(idle.need).not.toHaveBeenCalled()
  })
})
