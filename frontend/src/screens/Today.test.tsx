import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { SaveOutcome } from '../session'
import type { State } from '../storage'
import { fakeApi, json, sampleState } from '../test/helpers'
import { computeReply, readReply } from '../test/tracking'
import { Today } from './Today'

// The screen over a state it keeps, as the shell does, with every save recorded.
function Harness({ start, date, saves }: { start: State; date: string; saves: State[] }) {
  const [state, setState] = useState(start)
  const save = async (next: State): Promise<SaveOutcome> => {
    saves.push(next)
    setState(next)
    return { ok: true }
  }
  return <Today state={state} date={date} today="2026-11-02" onSave={save} />
}

// The sample plan with nothing logged yet.
function unlogged(): State {
  return { ...sampleState(), log: { version: 1, entries: [] } }
}

// Monday 2 November in a plan whose sample week has Monday's breakfast and lunch.
function show(start: State = unlogged()): { saves: State[]; user: UserEvent } {
  const saves: State[] = []
  const user = userEvent.setup()
  render(<Harness start={start} date="2026-11-02" saves={saves} />)
  return { saves, user }
}

function meal(name: string): HTMLElement {
  return screen.getByRole('region', { name: new RegExp(`^${name}`) })
}

async function readLunch(user: UserEvent): Promise<HTMLElement> {
  const lunch = meal('Lunch')
  await user.click(within(lunch).getByRole('button', { name: 'Swapped' }))
  await user.type(
    within(lunch).getByLabelText('What did you eat?'),
    '3 chapatis, some curd, half plate rice and bhindi sabzi',
  )
  await user.click(within(lunch).getByRole('button', { name: 'Read the meal' }))
  await within(lunch).findByRole('list', { name: 'Foods read' })
  return lunch
}

describe('the swap flow', () => {
  it('asks to confirm each food and blocks saving while one needs an amount or a unit', async () => {
    const calls = fakeApi({
      '/v1/log/read': () => json(readReply()),
      '/v1/log/compute': () => json(computeReply()),
    })
    const { saves, user } = show()
    const lunch = await readLunch(user)

    const save = within(lunch).getByRole('button', { name: 'Save the meal' })
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).toHaveAccessibleDescription(
      'Before saving: confirm Chapati; give an amount for Curd; choose a unit for Steamed rice.',
    )
    await user.click(save)
    expect(saves).toHaveLength(0)

    const foods = within(lunch).getByRole('list', { name: 'Foods read' })
    const curd = within(foods).getByRole('listitem', { name: /^Curd/ })
    expect(curd).toHaveTextContent('Needs an amount')
    const rice = within(foods).getByRole('listitem', { name: /^Steamed rice/ })
    expect(rice).toHaveTextContent('Unit not known: plate')

    await user.click(within(foods).getByRole('button', { name: 'Confirm Chapati' }))
    expect(save).toHaveAccessibleDescription(
      'Before saving: give an amount for Curd; choose a unit for Steamed rice.',
    )

    await user.type(within(curd).getByLabelText('Amount'), '100')
    await user.selectOptions(within(curd).getByLabelText('Unit'), 'g')
    await user.click(within(curd).getByRole('button', { name: 'Confirm Curd' }))
    expect(await within(curd).findByText('Confirmed')).toBeInTheDocument()
    expect(curd).toHaveTextContent('100 g: 100 g, 63 kcal')
    expect(calls.find((call) => call.path === '/v1/log/compute')?.body).toEqual({
      items: [
        { food_id: 'chapati', amount: 3, unit: 'piece' },
        { food_id: 'curd', amount: 100, unit: 'g' },
      ],
    })
    expect(save).toHaveAccessibleDescription('Before saving: choose a unit for Steamed rice.')

    await user.selectOptions(within(rice).getByLabelText('Unit'), 'katori')
    await user.click(within(rice).getByRole('button', { name: 'Confirm Steamed rice' }))
    await within(rice).findByText('Confirmed')
    await waitFor(() => expect(save).not.toHaveAttribute('aria-disabled', 'true'))
    await user.click(save)

    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0]!.log.entries.at(-1)).toEqual({
      date: '2026-11-02',
      slot_id: 'lunch',
      status: 'swapped',
      items: [
        { food_id: 'chapati', amount: 3, unit: 'piece' },
        { food_id: 'curd', amount: 100, unit: 'g' },
        { food_id: 'steamed_rice', amount: 0.5, unit: 'katori' },
      ],
    })
  })

  it('lists a food the data lacks as missing and leaves it out of the totals', async () => {
    const calls = fakeApi({
      '/v1/log/read': () => json(readReply()),
      '/v1/log/compute': () => json(computeReply()),
    })
    const { user } = show()
    const lunch = await readLunch(user)

    const foods = within(lunch).getByRole('list', { name: 'Foods read' })
    const sabzi = within(foods).getByRole('listitem', { name: /^bhindi sabzi/ })
    expect(sabzi).toHaveTextContent('Missing: not in the data, so it is left out of the totals')
    expect(within(sabzi).queryByRole('button')).not.toBeInTheDocument()
    const totals = within(lunch).getByRole('table', { name: 'Total of the foods counted' })
    expect(within(totals).getByRole('row', { name: /^Energy/ })).toHaveTextContent('357 kcal')
    expect(lunch).toHaveTextContent('Left out of the totals: bhindi sabzi, not in the data.')

    const curd = within(foods).getByRole('listitem', { name: /^Curd/ })
    await user.type(within(curd).getByLabelText('Amount'), '100')
    await user.selectOptions(within(curd).getByLabelText('Unit'), 'g')
    await user.click(within(curd).getByRole('button', { name: 'Confirm Curd' }))

    // The total is the server's for what was sent, and bhindi sabzi was not sent.
    await waitFor(() =>
      expect(within(totals).getByRole('row', { name: /^Energy/ })).toHaveTextContent('420 kcal'),
    )
    const sent = calls.find((call) => call.path === '/v1/log/compute')?.body as {
      items: { food_id: string }[]
    }
    expect(sent.items.map((item) => item.food_id)).toEqual(['chapati', 'curd'])
  })
})

it('needs nothing from the API to show the meals of the date', () => {
  const fetch = vi.fn<typeof globalThis.fetch>()
  vi.stubGlobal('fetch', fetch)
  show()

  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Monday 2 November')
  expect(fetch).not.toHaveBeenCalled()
})
