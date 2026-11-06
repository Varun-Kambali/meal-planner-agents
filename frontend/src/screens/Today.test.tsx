import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { SaveOutcome } from '../session'
import { ValidationFailed, type State } from '../storage'
import { catchDownloads, fakeApi, json, sampleState } from '../test/helpers'
import { computeReply, readReply } from '../test/tracking'
import { Today } from './Today'

// The screen over a state it keeps, as the shell does, with every save it is asked for recorded.
// `outcome` stands for the browser's storage; a state it refuses as not fitting is not kept.
function Harness({
  start,
  date,
  saves,
  outcome = () => ({ ok: true }),
}: {
  start: State
  date: string
  saves: State[]
  outcome?: (next: State) => SaveOutcome | Promise<SaveOutcome>
}) {
  const [state, setState] = useState(start)
  // As the shell does, the state changes once the browser has saved it.
  const save = async (next: State): Promise<SaveOutcome> => {
    saves.push(next)
    const result = await outcome(next)
    if (result.ok || !(result.error instanceof ValidationFailed)) setState(next)
    return result
  }
  return <Today state={state} date={date} today="2026-11-02" onSave={save} />
}

// The sample plan with nothing logged yet.
function unlogged(): State {
  return { ...sampleState(), log: { version: 1, entries: [] } }
}

// Monday 2 November in a plan whose sample week has Monday's breakfast and lunch.
function show(
  start: State = unlogged(),
  outcome?: (next: State) => SaveOutcome | Promise<SaveOutcome>,
): { saves: State[]; user: UserEvent } {
  const saves: State[] = []
  const user = userEvent.setup()
  render(<Harness start={start} date="2026-11-02" saves={saves} outcome={outcome} />)
  return { saves, user }
}

async function typeMeal(user: UserEvent, where: HTMLElement): Promise<void> {
  await user.click(within(where).getByRole('button', { name: 'Swapped' }))
  await user.type(
    within(where).getByLabelText('What did you eat?'),
    '3 chapatis, some curd, half plate rice and bhindi sabzi',
  )
  await user.click(within(where).getByRole('button', { name: 'Read the meal' }))
}

function meal(name: string): HTMLElement {
  return screen.getByRole('region', { name: new RegExp(`^${name}`) })
}

async function readLunch(user: UserEvent): Promise<HTMLElement> {
  const lunch = meal('Lunch')
  await typeMeal(user, lunch)
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

it('starts each food afresh when the meal is read again', async () => {
  const second = readReply()
  second.items = [
    second.items[0]!,
    { ...second.items[1]!, name: 'dal', food_id: 'dal_tadka', food_name: 'Dal tadka' },
  ]
  let reads = 0
  fakeApi({ '/v1/log/read': () => json((reads += 1) === 1 ? readReply() : second) })
  const { user } = show()
  const lunch = await readLunch(user)
  const curd = within(lunch).getByRole('listitem', { name: /^Curd/ })

  await user.type(within(curd).getByLabelText('Amount'), '50')
  await user.click(within(lunch).getByRole('button', { name: 'Read it again' }))

  const dal = await within(lunch).findByRole('listitem', { name: /^Dal tadka/ })
  expect(within(dal).getByLabelText('Amount')).toHaveValue('')
})

it("works out one food's numbers at a time, since each compute sends every counted food", async () => {
  let answer: (response: Response) => void = () => undefined
  const calls = fakeApi({
    '/v1/log/read': () => json(readReply()),
    '/v1/log/compute': () => new Promise<Response>((resolve) => (answer = resolve)),
  })
  const { user } = show()
  const lunch = await readLunch(user)
  const curd = within(lunch).getByRole('listitem', { name: /^Curd/ })
  const rice = within(lunch).getByRole('listitem', { name: /^Steamed rice/ })

  await user.type(within(curd).getByLabelText('Amount'), '100')
  await user.selectOptions(within(curd).getByLabelText('Unit'), 'g')
  await user.click(within(curd).getByRole('button', { name: 'Confirm Curd' }))
  await user.selectOptions(within(rice).getByLabelText('Unit'), 'katori')
  const confirmRice = within(rice).getByRole('button', { name: 'Confirm Steamed rice' })
  expect(confirmRice).toHaveAttribute('aria-disabled', 'true')
  await user.click(confirmRice)
  expect(calls.filter((call) => call.path === '/v1/log/compute')).toHaveLength(1)

  answer(json(computeReply()))
  await within(curd).findByText('Confirmed')
  await waitFor(() => expect(confirmRice).not.toHaveAttribute('aria-disabled', 'true'))
})

it("keeps a food confirmed while another food's numbers are worked out", async () => {
  let answer: (response: Response) => void = () => undefined
  fakeApi({
    '/v1/log/read': () => json(readReply()),
    '/v1/log/compute': () => new Promise<Response>((resolve) => (answer = resolve)),
  })
  const { user } = show()
  const lunch = await readLunch(user)
  const chapati = within(lunch).getByRole('listitem', { name: /^Chapati/ })
  const curd = within(lunch).getByRole('listitem', { name: /^Curd/ })

  await user.type(within(curd).getByLabelText('Amount'), '100')
  await user.selectOptions(within(curd).getByLabelText('Unit'), 'g')
  await user.click(within(curd).getByRole('button', { name: 'Confirm Curd' }))
  await user.click(within(chapati).getByRole('button', { name: 'Confirm Chapati' }))
  expect(chapati).toHaveTextContent('Confirmed')
  answer(json(computeReply()))

  await within(curd).findByText('Confirmed')
  expect(chapati).toHaveTextContent('Confirmed')
})

describe('the swap flow when a request fails', () => {
  it('says why the meal was not read, and reads it again when asked', async () => {
    let reads = 0
    fakeApi({
      '/v1/log/read': () => {
        reads += 1
        return reads === 1
          ? json({ detail: 'too many requests, try again later' }, 429, { 'Retry-After': '20' })
          : json(readReply())
      },
    })
    const { user } = show()
    const lunch = meal('Lunch')
    await typeMeal(user, lunch)

    const alert = await within(lunch).findByRole('alert')
    expect(alert).toHaveTextContent(
      'The meal was not read. The API has had too many requests. Try again in 20 seconds.',
    )
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    await within(lunch).findByRole('list', { name: 'Foods read' })
    expect(reads).toBe(2)
  })

  it('searches for another food only when asked, and says why its numbers were refused', async () => {
    const calls = fakeApi({
      '/v1/log/read': () => json(readReply()),
      '/v1/foods/search': () =>
        json([{ id: 'paneer', name: 'Paneer', kind: 'ingredient', units: ['g', 'cube'] }]),
      '/v1/log/compute': () =>
        json(
          {
            detail: [
              {
                loc: ['body', 'items', 0],
                msg: 'item 1 (paneer): amount 9000 g is over the limit of 5000 g',
              },
            ],
          },
          422,
        ),
    })
    const { user } = show()
    const lunch = await readLunch(user)
    const chapati = within(lunch).getByRole('listitem', { name: /^Chapati/ })

    await user.click(within(chapati).getByRole('button', { name: 'Change Chapati' }))
    await user.click(within(chapati).getByRole('button', { name: 'Change food' }))
    const name = within(chapati).getByLabelText('Food name')
    expect(name).toHaveFocus()
    await user.clear(name)
    await user.type(name, 'paneer')
    expect(calls.filter((call) => call.path.startsWith('/v1/foods/search'))).toHaveLength(0)
    await user.click(within(chapati).getByRole('button', { name: 'Search' }))
    await user.click(await within(chapati).findByRole('button', { name: 'Use Paneer' }))

    expect(calls.filter((call) => call.path.startsWith('/v1/foods/search'))).toEqual([
      expect.objectContaining({ path: '/v1/foods/search?q=paneer' }),
    ])
    const amount = within(chapati).getByLabelText('Amount')
    expect(amount).toHaveFocus()
    const unit = within(chapati).getByLabelText('Unit')
    expect(
      within(unit)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Choose a unit', 'g', 'cube'])
    await user.clear(amount)
    await user.type(amount, '9000')
    await user.selectOptions(unit, 'g')
    await user.click(within(chapati).getByRole('button', { name: 'Confirm Paneer' }))

    const alert = await within(chapati).findByRole('alert')
    expect(alert).toHaveTextContent(
      'The numbers were not worked out. The API said: item 1 (paneer): amount 9000 g is over the limit of 5000 g.',
    )
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(
      within(lunch).getByRole('button', { name: 'Save the meal' }),
    ).toHaveAccessibleDescription(expect.stringContaining('finish changing Chapati'))
  })
})

describe('logging a meal with one tap', () => {
  it('logs a meal as planned with the recipe at its final grams, and keeps the focus there', async () => {
    const { saves, user } = show()
    const breakfast = meal('Breakfast')
    const button = within(breakfast).getByRole('button', { name: 'As planned' })

    await user.click(button)

    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0]!.log.entries).toEqual([
      {
        date: '2026-11-02',
        slot_id: 'breakfast',
        status: 'as_planned',
        items: [
          { food_id: 'banana', amount: 182.19, unit: 'g' },
          { food_id: 'oats', amount: 99.38, unit: 'g' },
          { food_id: 'curd', amount: 49.69, unit: 'g' },
        ],
      },
    ])
    expect(breakfast).toHaveTextContent('Logged: as planned.')
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('Breakfast logged as planned.'),
    )
    expect(button).toHaveFocus()
  })

  it('saves one meal at a time, so a second tap cannot drop the first', async () => {
    let release: () => void = () => undefined
    const late = new Promise<void>((resolve) => (release = resolve))
    const { saves, user } = show(unlogged(), async () => {
      if (saves.length === 1) await late
      return { ok: true }
    })
    const lunchPlanned = within(meal('Lunch')).getByRole('button', { name: 'As planned' })

    await user.click(within(meal('Breakfast')).getByRole('button', { name: 'Skipped' }))
    expect(lunchPlanned).toHaveAttribute('aria-disabled', 'true')
    await user.click(lunchPlanned)
    expect(saves).toHaveLength(1)
    release()
    await waitFor(() => expect(meal('Breakfast')).toHaveTextContent('Logged: skipped.'))
    await waitFor(() => expect(lunchPlanned).not.toHaveAttribute('aria-disabled'))
    await user.click(lunchPlanned)

    await waitFor(() => expect(saves).toHaveLength(2))
    expect(saves[1]!.log.entries.map((entry) => [entry.slot_id, entry.status])).toEqual([
      ['breakfast', 'skipped'],
      ['lunch', 'as_planned'],
    ])
  })

  it("replaces the meal's entry when it is logged again, and a skipped meal lists no food", async () => {
    const { saves, user } = show()
    const breakfast = meal('Breakfast')

    await user.click(within(breakfast).getByRole('button', { name: 'As planned' }))
    await user.click(within(breakfast).getByRole('button', { name: 'Skipped' }))

    await waitFor(() => expect(saves).toHaveLength(2))
    expect(saves[1]!.log.entries).toEqual([
      { date: '2026-11-02', slot_id: 'breakfast', status: 'skipped', items: [] },
    ])
    expect(breakfast).toHaveTextContent('Logged: skipped.')
  })

  it('offers all four for a recipe that misses a check, as it has numbers', async () => {
    const start = unlogged()
    start.week!.recipes[1]!.failures = [
      { check: 'slot_protein', message: 'protein 31 g, the meal needs at least 40 g' },
    ]
    const { saves, user } = show(start)
    const lunch = meal('Lunch')
    const asPlanned = within(lunch).getByRole('button', { name: 'As planned' })

    expect(asPlanned).not.toHaveAttribute('aria-disabled')
    expect(within(lunch).getByRole('button', { name: 'Different amount' })).not.toHaveAttribute(
      'aria-disabled',
    )
    await user.click(asPlanned)

    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0]!.log.entries[0]).toMatchObject({ slot_id: 'lunch', status: 'as_planned' })
  })

  it('offers only swapped or skipped for a recipe with no numbers, or a meal with none', async () => {
    const start = unlogged()
    start.week!.recipes[1]!.nutrition = null
    start.week!.recipes[1]!.failures = [
      { check: 'nutrition_failed', message: 'nutrition-mcp could not work out this recipe' },
    ]
    const { saves, user } = show(start)
    const lunch = meal('Lunch')
    const asPlanned = within(lunch).getByRole('button', { name: 'As planned' })

    expect(asPlanned).toHaveAttribute('aria-disabled', 'true')
    expect(asPlanned).toHaveAccessibleDescription(
      "This recipe's numbers could not be worked out, so the meal can be logged only as swapped or skipped.",
    )
    expect(within(lunch).getByRole('button', { name: 'Different amount' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    await user.click(asPlanned)
    const snack = meal('Evening snack')
    expect(within(snack).getByRole('button', { name: 'As planned' })).toHaveAccessibleDescription(
      'No recipe was written for this meal, so it can be logged only as swapped or skipped.',
    )
    await user.click(within(snack).getByRole('button', { name: 'Skipped' }))

    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0]!.log.entries.map((entry) => entry.slot_id)).toEqual(['snack'])
  })

  it('scales every ingredient by half, one and a half, or the grams of the whole meal', async () => {
    const { saves, user } = show()
    const breakfast = meal('Breakfast')
    const different = within(breakfast).getByRole('button', { name: 'Different amount' })

    await user.click(different)
    expect(different).toHaveAttribute('aria-expanded', 'true')
    expect(within(breakfast).getByRole('radio', { name: 'Half' })).toHaveFocus()
    await user.click(within(breakfast).getByRole('radio', { name: 'One and a half' }))
    await user.click(within(breakfast).getByRole('button', { name: 'Save the amount' }))
    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0]!.log.entries[0]).toMatchObject({ status: 'different_amount' })
    expect(saves[0]!.log.entries[0]!.items.map((item) => item.amount)).toEqual([
      273.28, 149.07, 74.54,
    ])
    expect(different).toHaveFocus()

    await user.click(different)
    await user.click(within(breakfast).getByRole('radio', { name: 'A weight in grams' }))
    const grams = within(breakfast).getByLabelText('Grams of the whole meal')
    expect(grams).toHaveAccessibleDescription(expect.stringContaining('weighs 331 g'))
    await user.type(grams, '6000')
    await user.click(within(breakfast).getByRole('button', { name: 'Save the amount' }))
    expect(grams).toHaveAccessibleDescription(
      expect.stringContaining('Give the grams as a number above 0 and at most 5000.'),
    )
    expect(saves).toHaveLength(1)
    await user.clear(grams)
    await user.type(grams, '200')
    await user.click(within(breakfast).getByRole('button', { name: 'Save the amount' }))

    await waitFor(() => expect(saves).toHaveLength(2))
    expect(saves[1]!.log.entries[0]!.items.map((item) => item.amount)).toEqual([110, 60, 30])
  })

  it('says the log is full before saving, and starts a new one only once it is kept as a file', async () => {
    const downloads = catchDownloads()
    const { saves, user } = show(sampleState())

    await user.click(within(meal('Breakfast')).getByRole('button', { name: 'Skipped' }))

    const notice = await screen.findByRole('region', { name: 'The log is full' })
    expect(notice).toHaveTextContent(
      'A log covers at most 7 days in a row. Yours has meals on Monday 26 October only. Logging Breakfast on Monday 2 November would make it 8 days, so nothing has been saved.',
    )
    expect(within(notice).getByRole('heading')).toHaveFocus()
    expect(saves).toHaveLength(0)
    expect(
      within(notice).queryByRole('button', { name: 'Start a new log with this meal' }),
    ).not.toBeInTheDocument()

    await user.click(within(notice).getByRole('button', { name: 'Save the log as a file' }))
    expect(downloads).toHaveLength(1)
    expect(JSON.parse(await downloads[0]!.text()).log).toEqual(sampleState().log)
    await user.click(within(notice).getByRole('button', { name: 'Start a new log with this meal' }))

    await waitFor(() => expect(saves).toHaveLength(1))
    expect(saves[0]!.log.entries).toEqual([
      { date: '2026-11-02', slot_id: 'breakfast', status: 'skipped', items: [] },
    ])
    expect(screen.queryByRole('region', { name: 'The log is full' })).not.toBeInTheDocument()
  })

  it('says a meal was not saved when the browser refuses the log, and changes nothing', async () => {
    const refused = new ValidationFailed([
      { path: '/log/entries/0/slot_id', message: 'the plan has no such slot' },
    ])
    const { user } = show(unlogged(), () => ({ ok: false, error: refused }))
    const breakfast = meal('Breakfast')

    await user.click(within(breakfast).getByRole('button', { name: 'Skipped' }))

    expect(await within(breakfast).findByRole('alert')).toHaveTextContent(
      'Not saved: the data does not fit the file format: /log/entries/0/slot_id the plan has no such slot.',
    )
    expect(breakfast).toHaveTextContent('Not logged yet.')
  })
})
