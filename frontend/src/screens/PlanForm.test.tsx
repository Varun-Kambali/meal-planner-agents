import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { fromServer } from '../planForm'
import type { SaveOutcome, Update } from '../session'
import { emptyState, StorageUnavailable, type State } from '../storage'
import { CATALOGUE, catchDownloads, fakeApi, json, sampleState } from '../test/helpers'
import { useCatalogue } from '../useCatalogue'
import { PlanForm, type PlanFormProps } from './PlanForm'

function saver(outcome: SaveOutcome = { ok: true }) {
  return vi.fn<(state: State) => Promise<SaveOutcome>>(async () => outcome)
}

// The form with the catalogue the shell would give it (useCatalogue).
function Form(props: Omit<PlanFormProps, 'catalogue'>) {
  return <PlanForm {...props} catalogue={useCatalogue()} />
}

function show(
  state: State,
  options: { onSave?: ReturnType<typeof saver>; refused?: ReturnType<typeof fromServer> } = {},
) {
  const onSave = options.onSave ?? saver()
  // As the shell does: the change is applied to the state, and the mock sees the result.
  const save = (update: Update): Promise<SaveOutcome> => onSave(update(state))
  render(<Form state={state} refused={options.refused ?? []} onSave={save} blocked={null} />)
  return onSave
}

function group(name: string): HTMLElement {
  return screen.getByRole('group', { name })
}

describe('the plan form', () => {
  it('shows the field error from a 422 under that field and in the list at the top', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const refused = fromServer(
      [
        { loc: ['body', 'food', 'liked_foods'], msg: "'jackfruit' is not in the catalogue" },
        {
          loc: ['body'],
          msg: 'Value error, slots: kcal_share values must add up to 1, they add up to 0.95',
        },
      ],
      ['body'],
    )

    show(sampleState(), { refused })

    const liked = screen.getByLabelText('Foods you like')
    expect(liked).toHaveAccessibleDescription(
      expect.stringContaining("'jackfruit' is not in the catalogue"),
    )
    expect(liked).toHaveAttribute('aria-invalid', 'true')
    expect(group('Meals of the day')).toHaveTextContent(
      'Calorie shares add up to 95% of the day; they must add up to 100%.',
    )
    const summary = screen.getByRole('region', { name: /The API refused the plan/ })
    const link = within(summary).getByRole('link', { name: /jackfruit/ })
    expect(link).toHaveAttribute('href', `#${liked.id}`)
  })

  it('refuses slot shares that do not add up to 100% of the day', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const onSave = show(sampleState())

    const lunch = group('Meal 2')
    const share = within(lunch).getByLabelText("Share of the day's calories (%)")
    await user.clear(share)
    await user.type(share, '25')
    await user.click(screen.getByRole('button', { name: 'Save the plan' }))

    expect(onSave).not.toHaveBeenCalled()
    const summary = screen.getByRole('region', { name: /problem with the plan/ })
    expect(summary).toHaveFocus()
    expect(summary).toHaveTextContent(
      'Calorie shares add up to 95% of the day; they must add up to 100%.',
    )
    expect(group('Meals of the day')).toHaveTextContent('Calorie shares add up to 95%')
  })

  it('saves a new plan as rules the API accepts, with ids made from the names', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const onSave = show(emptyState())

    await user.type(screen.getByLabelText('Plan name'), 'Weekdays at the office')
    const day = group('Day type 1')
    await user.type(within(day).getByLabelText('Name'), 'Training day')
    await user.type(within(day).getByLabelText('Calories (kcal)'), '2400')
    await user.type(within(day).getByLabelText('Protein, at least (g)'), '140')
    await user.type(within(day).getByLabelText('Carbohydrate (g)'), '280')
    await user.type(within(day).getByLabelText('Fat (g)'), '75')
    await user.type(within(day).getByLabelText('Saturated fat, at most (g)'), '25')
    await user.type(within(day).getByLabelText('Fibre, at least (g)'), '30')
    const meal = group('Meal 1')
    await user.type(within(meal).getByLabelText('Name'), 'Lunch box')
    await user.type(within(meal).getByLabelText('Eaten at'), '13:00')
    await user.type(within(meal).getByLabelText("Share of the day's calories (%)"), '100')
    await user.type(within(meal).getByLabelText("Share of the day's protein (%)"), '100')
    await user.type(within(meal).getByLabelText('Kind of dish'), 'packed lunch')
    await user.selectOptions(within(meal).getByLabelText('When it is cooked'), 'evening_before')
    await user.type(within(meal).getByLabelText('Most active minutes'), '30')
    await user.type(within(meal).getByLabelText('Most minutes in all'), '60')
    await user.click(within(group('Kitchen gear you own')).getByLabelText('Pressure cooker'))
    await user.click(within(group('Diet')).getByLabelText('Vegetarian'))
    await user.type(screen.getByLabelText('Cuisines to draw on'), 'South Indian{Enter}Japanese')
    await user.click(within(group('Shopping days')).getByLabelText('Saturday'))
    await screen.findAllByRole('option', { name: 'Paneer' })
    await user.selectOptions(screen.getByLabelText('Foods you like'), 'paneer')
    await user.click(screen.getByRole('button', { name: 'Add to foods you like' }))
    expect(screen.getByRole('list', { name: 'Foods you like, chosen' })).toHaveTextContent('Paneer')
    await user.click(screen.getByRole('button', { name: 'Save the plan' }))

    expect(onSave).toHaveBeenCalledTimes(1)
    const saved = onSave.mock.calls[0]![0]
    expect(saved.week).toBeNull()
    expect(saved.rules).toEqual({
      version: 1,
      name: 'Weekdays at the office',
      day_types: [
        {
          id: 'training_day',
          name: 'Training day',
          kcal: 2400,
          protein_g: 140,
          carbs_g: 280,
          fat_g: 75,
          sat_fat_g_max: 25,
          fibre_g_min: 30,
          extra: [],
        },
      ],
      week: {
        mon: 'training_day',
        tue: 'training_day',
        wed: 'training_day',
        thu: 'training_day',
        fri: 'training_day',
        sat: 'training_day',
        sun: 'training_day',
      },
      slots: [
        {
          id: 'lunch_box',
          name: 'Lunch box',
          eat_at: '13:00',
          kcal_share: 1,
          protein_share: 1,
          style: 'packed lunch',
          cook_when: 'evening_before',
          max_active_min: 30,
          max_total_min: 60,
        },
      ],
      gear: ['pressure_cooker'],
      food: {
        diet: 'veg',
        exclude_allergens: [],
        exclude_foods: [],
        liked_foods: ['paneer'],
        cuisines: ['South Indian', 'Japanese'],
      },
      shopping_days: ['sat'],
    })
    expect(await screen.findByText('Plan saved in this browser.')).toBeInTheDocument()
  })

  it('keeps the ids of a saved plan when its names change', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const state = { ...sampleState(), week: null }
    const onSave = show(state)

    const name = within(group('Meal 1')).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Early breakfast')
    await user.click(screen.getByRole('button', { name: 'Save the plan' }))

    const saved = onSave.mock.calls[0]![0].rules!
    expect(saved.slots.map((slot) => slot.id)).toEqual(['breakfast', 'lunch', 'snack', 'dinner'])
    expect(saved.slots[0]!.name).toBe('Early breakfast')
  })

  it('asks before a change to the plan clears the week written for it', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const onSave = show(sampleState())

    const name = screen.getByLabelText('Plan name')
    await user.clear(name)
    await user.type(name, 'Office weekday, lighter')
    await user.click(screen.getByRole('button', { name: 'Save the plan' }))

    expect(onSave).not.toHaveBeenCalled()
    const question = screen.getByRole('region', { name: 'This plan has a written week' })
    await user.click(within(question).getByRole('button', { name: 'Save and clear the week' }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave.mock.calls[0]![0].week).toBeNull()
    expect(onSave.mock.calls[0]![0].rules!.name).toBe('Office weekday, lighter')
  })

  // The sample plan without its snack, whose log entry is the one the plan no longer covers.
  async function dropSnack(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    await user.click(screen.getByRole('button', { name: 'Remove meal 3' }))
    for (const [label, value] of [
      ["Share of the day's calories (%)", '45'],
      ["Share of the day's protein (%)", '45'],
    ] as const) {
      const field = within(group('Meal 3')).getByLabelText(label)
      await user.clear(field)
      await user.type(field, value)
    }
    await user.click(screen.getByRole('button', { name: 'Save the plan' }))
  }

  it('offers to remove the logged meals of a meal the plan drops, and removes only those', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const state: State = { ...sampleState(), week: null }
    const onSave = show(state)

    await dropSnack(user)

    expect(onSave).not.toHaveBeenCalled()
    const offer = screen.getByRole('region', { name: 'This plan drops meals you have logged' })
    expect(within(offer).getByRole('heading', { level: 2 })).toHaveFocus()
    expect(offer).toHaveTextContent('the file keeps them')
    expect(within(offer).getByRole('button', { name: 'Export a copy first' })).toBeInTheDocument()
    await user.click(
      within(offer).getByRole('button', {
        name: 'Save and remove the 1 logged meal for Evening snack',
      }),
    )

    expect(onSave).toHaveBeenCalledTimes(1)
    const saved = onSave.mock.calls[0]![0]
    expect(saved.rules!.slots.map((slot) => slot.id)).toEqual(['breakfast', 'lunch', 'dinner'])
    expect(saved.log.entries.map((entry) => entry.slot_id)).toEqual(['breakfast', 'lunch'])
    expect(await screen.findByText('Plan saved in this browser.')).toBeInTheDocument()
  })

  it('changes nothing when the offer to remove logged meals is turned down', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const onSave = show({ ...sampleState(), week: null })

    await dropSnack(user)
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))

    expect(onSave).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('region', { name: 'This plan drops meals you have logged' }),
    ).not.toBeInTheDocument()
  })

  it('exports the whole state, log included, from the offer to remove logged meals', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const files = catchDownloads()
    const user = userEvent.setup()
    show({ ...sampleState(), week: null })

    await dropSnack(user)
    await user.click(screen.getByRole('button', { name: 'Export a copy first' }))

    expect(files).toHaveLength(1)
    const exported = JSON.parse(await files[0]!.text()) as State
    expect(exported.log.entries.map((entry) => entry.slot_id)).toEqual([
      'breakfast',
      'lunch',
      'snack',
    ])
  })

  it('saves an unchanged plan without touching its week', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const state = sampleState()
    const onSave = show(state)

    await user.click(screen.getByRole('button', { name: 'Save the plan' }))

    expect(onSave).toHaveBeenCalledWith(state)
  })

  it('says so when the browser does not save the plan', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    const onSave = saver({ ok: false, error: new StorageUnavailable() })
    show({ ...sampleState(), week: null }, { onSave })

    await user.click(screen.getByRole('button', { name: 'Save the plan' }))

    expect(await screen.findByText(/Not saved in this browser/)).toHaveTextContent(
      'this browser does not let the page keep data',
    )
  })

  it('keeps the focus in the form when a row it was on is removed', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    show({ ...sampleState(), week: null })
    await screen.findAllByRole('option', { name: 'Brinjal' })

    await user.click(screen.getByRole('button', { name: 'Remove Paneer from foods you like' }))
    await waitFor(() => expect(screen.getByLabelText('Foods you like')).toHaveFocus())

    await user.click(screen.getByRole('button', { name: 'Remove meal 2' }))
    await waitFor(() => expect(group('Meals of the day')).toHaveFocus())

    await user.click(within(group('Day type 1')).getByRole('button', { name: 'Remove target 1' }))
    await waitFor(() =>
      expect(
        within(group('Day type 1')).getByRole('group', { name: 'Other daily targets' }),
      ).toHaveFocus(),
    )

    await user.click(screen.getByRole('button', { name: 'Remove day type 2' }))
    await waitFor(() => expect(group('Day types')).toHaveFocus())
  })

  it('says when the food list cannot be loaded, and loads it on asking again', async () => {
    let answers = 0
    fakeApi({
      '/v1/catalogue': () => {
        answers += 1
        if (answers === 1) throw new TypeError('Failed to fetch')
        return json(CATALOGUE)
      },
    })
    const user = userEvent.setup()
    show(sampleState())

    expect(await screen.findByText(/The food list could not be loaded/)).toHaveTextContent(
      'the API could not be reached',
    )
    await user.click(screen.getByRole('button', { name: 'Load the food list again' }))

    expect(await screen.findAllByRole('option', { name: 'Paneer' })).not.toHaveLength(0)
    expect(screen.queryByText(/The food list could not be loaded/)).not.toBeInTheDocument()
  })
})
