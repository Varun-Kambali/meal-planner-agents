import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import { STORAGE_KEY, type State } from '../storage'
import { FakeStream, fakeApi, json, sampleState, sampleWeek } from '../test/helpers'
import type { CheckedRecipe, WeekResult } from '../types'

function seed(state: State): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...state }))
}

function saved(): State {
  return JSON.parse(localStorage.getItem(STORAGE_KEY)!) as State
}

// The sample week with Monday's lunch failing a check and Tuesday's lunch not written.
function failingWeek(): WeekResult {
  const week = sampleWeek()
  week.recipes[1]!.failures = [
    { check: 'slot_protein', message: 'protein 31 g, the meal needs at least 40 g' },
  ]
  week.recipes[1]!.attempts = 4
  week.unwritten = [['tue', 'lunch', 'timeout']]
  return week
}

// The table's cell for a day and a meal, found by its row and column headers.
function cell(day: string, meal: string): HTMLElement {
  const table = screen.getByRole('table')
  const columns = within(table)
    .getAllByRole('columnheader')
    .map((header) => header.textContent ?? '')
  const column = columns.findIndex((text) => text.startsWith(meal))
  const row = within(table)
    .getByRole('rowheader', { name: new RegExp(`^${day}`) })
    .closest('tr')!
  return row.children[column] as HTMLElement
}

async function findCell(day: string, meal: string): Promise<HTMLElement> {
  await screen.findByRole('table')
  return cell(day, meal)
}

beforeEach(async () => {
  window.location.hash = '#/week'
  // jsdom fires hashchange a moment later; let it go before the app is there to hear it.
  await new Promise((resolve) => setTimeout(resolve, 0))
})

afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
  window.location.hash = ''
})

describe('the week', () => {
  it('marks a failing recipe with its failure text, and a meal it could not write', async () => {
    seed({ ...sampleState(), week: failingWeek() })

    render(<App />)

    const lunch = await findCell('Monday', 'Lunch')
    expect(within(lunch).getByRole('link', { name: 'Paneer rice box' })).toHaveAttribute(
      'href',
      '#/recipe/mon/lunch',
    )
    expect(lunch).toHaveTextContent('Misses:')
    expect(lunch).toHaveTextContent('protein 31 g, the meal needs at least 40 g')
    expect(lunch).toHaveTextContent('Tried 4 times')
    expect(cell('Monday', 'Breakfast')).toHaveTextContent('Meets every check')
    expect(cell('Tuesday', 'Lunch')).toHaveTextContent(
      'Could not write: the model did not answer in time',
    )
  })

  it('leaves the focus where it is when the screen opens', async () => {
    seed({ ...sampleState(), week: failingWeek() })

    render(<App />)
    await findCell('Monday', 'Lunch')
    await new Promise((resolve) => setTimeout(resolve, 10))

    expect(document.body).toHaveFocus()
  })

  it('shows each meal as it is written, checked and repaired, then the week', async () => {
    seed({ ...sampleState(), week: null })
    const run = new FakeStream()
    const calls = fakeApi({ '/v1/week': () => run.response })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write the week' }))
    const days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
    for (const slot of ['breakfast', 'lunch', 'snack', 'dinner']) {
      run.send({ kind: 'slot_started', slot_id: slot, days: [...days], attempt: 1 })
    }
    run.send({ kind: 'slot_written', slot_id: 'breakfast', days: [...days], attempt: 1 })
    run.send({ kind: 'repair_started', slot_id: 'lunch', days: ['mon'], attempt: 2 })

    const progress = await screen.findByRole('region', { name: 'Writing the week' })
    await waitFor(() => expect(progress).toHaveTextContent('Lunch: Repairing, try 2 of 4'))
    expect(progress).toHaveTextContent('Breakfast: Checking')
    expect(progress).toHaveTextContent('Dinner: Writing')
    expect(within(progress).getByRole('progressbar')).toHaveAttribute('value', '1')
    const write = screen.getByRole('button', { name: 'Write the week' })
    expect(write).toHaveAttribute('aria-disabled', 'true')
    // Pressed again while the run goes, it starts nothing.
    await user.click(write)
    expect(calls).toHaveLength(1)
    expect(calls[0]!.body).toEqual(sampleState().rules)

    run.send({ kind: 'week_done', week: failingWeek() })
    run.end()

    expect(await findCell('Monday', 'Lunch')).toHaveTextContent('Misses:')
    expect(screen.queryByRole('region', { name: 'Writing the week' })).not.toBeInTheDocument()
    expect(saved().week).toEqual(failingWeek())
  })

  it('says the run stopped, and keeps the week it had, when the stream ends in an error', async () => {
    const before = failingWeek()
    seed({ ...sampleState(), week: before })
    const run = new FakeStream()
    fakeApi({ '/v1/week': () => run.response })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write a new week' }))
    run.send({ kind: 'error', step: 'week', message: 'the run stopped' })
    run.end()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The run stopped before the week was done: the run stopped.')
    expect(alert).toHaveTextContent('The week you had is unchanged.')
    expect(cell('Monday', 'Lunch')).toHaveTextContent('Misses:')
    expect(saved().week).toEqual(before)
  })

  it('stops the run when asked, and closes the connection', async () => {
    seed({ ...sampleState(), week: null })
    const run = new FakeStream()
    const calls = fakeApi({ '/v1/week': () => run.response })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write the week' }))
    await user.click(await screen.findByRole('button', { name: 'Stop' }))

    expect(await screen.findByText('Stopped. Nothing was changed.')).toBeInTheDocument()
    expect(calls[0]!.signal?.aborted).toBe(true)
    const write = screen.getByRole('button', { name: 'Write the week' })
    expect(write).toHaveAttribute('aria-disabled', 'false')
    expect(write).toHaveFocus()
  })

  it('says when the API cannot be reached', async () => {
    seed({ ...sampleState(), week: null })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write the week' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The API could not be reached; check the connection and try again.',
    )
  })

  it('says how long to wait when the API has had too many requests', async () => {
    seed({ ...sampleState(), week: null })
    fakeApi({
      '/v1/week': () => json({ detail: 'too many requests' }, 429, { 'Retry-After': '42' }),
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write the week' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 42 seconds.')
  })

  it('replaces one recipe, keeps the rest, and keeps the meals it could not write', async () => {
    seed({ ...sampleState(), week: failingWeek() })
    const run = new FakeStream()
    const calls = fakeApi({ '/v1/week/replace': () => run.response })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: "Replace Monday's Lunch" }))
    run.send({ kind: 'slot_started', slot_id: 'lunch', days: ['mon'], attempt: 1 })
    expect(await screen.findByRole('region', { name: "Writing Monday's Lunch" })).toHaveTextContent(
      'Lunch: Writing',
    )
    const result = sampleWeek()
    const fresh: CheckedRecipe = structuredClone(result.recipes[1]!)
    fresh.draft.title = 'Rajma rice bowl'
    result.recipes[1] = fresh
    result.unwritten = []
    run.send({ kind: 'week_done', week: result })
    run.end()

    const lunch = await findCell('Monday', 'Lunch')
    await waitFor(() => expect(lunch).toHaveTextContent('Rajma rice bowl'))
    expect(cell('Tuesday', 'Lunch')).toHaveTextContent('Could not write')
    expect(saved().week!.unwritten).toEqual([['tue', 'lunch', 'timeout']])
    expect(calls[0]!.body).toMatchObject({ day: 'mon', slot_id: 'lunch' })
  })

  it('says so when no new recipe could be written, and keeps the one it had', async () => {
    const before = failingWeek()
    seed({ ...sampleState(), week: before })
    const run = new FakeStream()
    fakeApi({ '/v1/week/replace': () => run.response })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: "Replace Monday's Lunch" }))
    // The server keeps the old recipe and names the meal as not written.
    const result = sampleWeek()
    result.unwritten = [['mon', 'lunch', 'rate_limited']]
    run.send({ kind: 'week_done', week: result })
    run.end()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "No new recipe could be written for Monday's Lunch: the model's rate limit was reached.",
    )
    expect(screen.getByRole('alert')).toHaveTextContent('The recipe you had is unchanged.')
    expect(screen.queryByText("Monday's Lunch is written again.")).not.toBeInTheDocument()
    expect(saved().week).toEqual(before)
  })

  it('keeps the recipe when a replacement is refused before it starts', async () => {
    seed({ ...sampleState(), week: failingWeek() })
    fakeApi({
      '/v1/week/replace': () =>
        json(
          {
            detail:
              "nutrition-mcp could not be reached to work out the recipes' numbers (step: recompute)",
          },
          503,
        ),
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: "Replace Monday's Lunch" }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The API said: nutrition-mcp could not be reached to work out the recipes' numbers (step: recompute).",
    )
    expect(screen.getByRole('alert')).toHaveTextContent('The recipe you had is unchanged.')
    expect(cell('Monday', 'Lunch')).toHaveTextContent('Paneer rice box')
  })

  it('shows one day at a time on a narrow screen, with the next and previous days', async () => {
    seed({ ...sampleState(), week: failingWeek() })
    // A Monday, the day a narrow screen opens on.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-02T09:00:00'))
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      })),
    )
    const user = userEvent.setup()
    render(<App />)

    const monday = await screen.findByRole('heading', { name: 'Monday', level: 2 })
    expect(monday).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next day: Tuesday' }))

    expect(screen.getByRole('heading', { name: 'Tuesday', level: 2 })).toBeInTheDocument()
    const tuesday = screen.getByRole('region', { name: 'Tuesday' })
    expect(
      within(tuesday).getByText('the model did not answer in time', { exact: false }),
    ).toHaveTextContent('Could not write: the model did not answer in time')
  })
})
