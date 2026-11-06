import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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
