import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { State } from '../storage'
import { CATALOGUE, sampleState } from '../test/helpers'
import type { FoodCatalogue, FoodList } from '../useCatalogue'
import { RecipePage } from './RecipePage'

function food(list: FoodList = { status: 'ready', rows: CATALOGUE }): FoodCatalogue {
  return { list, need: vi.fn<() => void>(), reload: vi.fn<() => void>() }
}

// The sample week, with Monday's breakfast missing a check, a fibre total that is a lower bound
// and no glycemic load.
function state(): State {
  const saved = sampleState()
  const breakfast = saved.week!.recipes[0]!
  breakfast.failures = [{ check: 'slot_kcal', message: 'calories 540, the meal needs 596 to 729' }]
  breakfast.portion_scale = 0.85
  breakfast.attempts = 2
  breakfast.nutrition!.total.fibre_g = 12.4
  breakfast.nutrition!.total_incomplete = { fibre_g: ['curd'] }
  return saved
}

describe('the recipe page', () => {
  it('shows the recipe with its grams, steps, numbers and what it misses', () => {
    render(<RecipePage state={state()} day="mon" slotId="breakfast" food={food()} />)

    expect(screen.getByRole('heading', { level: 1, name: 'Banana overnight oats' })).toBeVisible()
    expect(screen.getByText(/Monday, Breakfast at 08:00/)).toHaveTextContent('South Indian')
    const status = screen.getByText('Misses:').closest('section')!
    expect(status).toHaveTextContent('calories 540, the meal needs 596 to 729')

    const ingredients = screen.getByRole('table', { name: 'Ingredients for one serving' })
    const rows = within(ingredients).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual([
      'Banana182 g, 1½ pieces',
      'Oats99 g, 1¼ cups',
      'Curd50 g',
    ])
    expect(screen.getByText(/Amounts scaled by 0.85/)).toBeInTheDocument()

    const steps = screen.getByRole('list', { name: 'Steps' })
    expect(
      within(steps)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['Layer the oats, curd and banana in a jar.', 'Close the lid and chill overnight.'])

    const nutrients = screen.getByRole('table', { name: 'Nutrients in one serving' })
    expect(within(nutrients).getByRole('row', { name: /^Energy/ })).toHaveTextContent('663 kcal')
    expect(within(nutrients).getByRole('row', { name: /^Fibre/ })).toHaveTextContent(
      'at least 12 g',
    )
    expect(within(nutrients).getByRole('row', { name: /^Sugars/ })).toHaveTextContent('not known')
    expect(screen.getByText(/Fibre is at least this/)).toHaveTextContent('no value for Curd')
    expect(screen.getByText(/Glycemic load/)).toHaveTextContent(
      'not known: 3 of 3 ingredients have no value',
    )
  })

  it('shows a household measure in muted text, and grams alone where there is none', () => {
    render(<RecipePage state={state()} day="mon" slotId="breakfast" food={food()} />)

    const banana = screen.getByRole('row', { name: /^Banana/ })
    expect(within(banana).getByText(', 1½ pieces')).toHaveClass('measure')
    const curd = screen.getByRole('row', { name: /^Curd/ })
    expect(curd.querySelector('.measure')).toBeNull()
  })

  it('shows grams alone for a recipe saved before measures were shown', () => {
    const saved = state()
    saved.week!.recipes[0]!.measures = []
    render(<RecipePage state={saved} day="mon" slotId="breakfast" food={food()} />)

    const ingredients = screen.getByRole('table', { name: 'Ingredients for one serving' })
    const rows = within(ingredients).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual(['Banana182 g', 'Oats99 g', 'Curd50 g'])
  })

  it('names the catalogue foods a step quotes by id, once the catalogue has loaded', () => {
    const saved = state()
    saved.week!.recipes[0]!.draft.steps = ['Soak the moong_dal, then layer the oats in a jar.']
    const loaded = food()
    const { rerender } = render(
      <RecipePage state={saved} day="mon" slotId="breakfast" food={food({ status: 'loading' })} />,
    )

    const steps = screen.getByRole('list', { name: 'Steps' })
    expect(steps).toHaveTextContent('Soak the moong_dal, then layer the oats in a jar.')

    rerender(<RecipePage state={saved} day="mon" slotId="breakfast" food={loaded} />)

    expect(steps).toHaveTextContent('Soak the moong dal, then layer the oats in a jar.')
    // What the week holds is left as the model wrote it.
    expect(saved.week!.recipes[0]!.draft.steps[0]).toContain('moong_dal')
  })

  it('shows the steps as written when the catalogue could not be loaded', () => {
    const saved = state()
    saved.week!.recipes[0]!.draft.steps = ['Soak the moong_dal.']
    const failed = food({ status: 'failed', message: 'the API could not be reached' })
    render(<RecipePage state={saved} day="mon" slotId="breakfast" food={failed} />)

    expect(screen.getByRole('list', { name: 'Steps' })).toHaveTextContent('Soak the moong_dal.')
  })

  it('asks the shell for the catalogue when it opens', () => {
    const asked = food({ status: 'idle' })
    render(<RecipePage state={state()} day="mon" slotId="breakfast" food={asked} />)

    expect(asked.need).toHaveBeenCalled()
  })

  it('says so when the week has no recipe for that day and meal', () => {
    render(<RecipePage state={state()} day="tue" slotId="dinner" food={food()} />)

    expect(screen.getByRole('heading', { level: 1, name: 'No recipe here' })).toBeVisible()
    expect(screen.getByText(/no recipe for Tuesday's Dinner/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the week' })).toHaveAttribute('href', '#/week')
  })

  it('prints from the browser', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
    const user = userEvent.setup()
    render(<RecipePage state={state()} day="mon" slotId="lunch" food={food()} />)

    await user.click(screen.getByRole('button', { name: 'Print this recipe' }))

    expect(print).toHaveBeenCalledTimes(1)
  })

  it('lists every recipe of the week, each on a page of its own when printed', () => {
    const { container } = render(
      <RecipePage state={state()} day={null} slotId={null} food={food()} />,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Recipes for the week' })).toBeVisible()
    const recipes = container.querySelectorAll('article.recipe')
    expect([...recipes].map((article) => article.querySelector('h2')?.textContent)).toEqual([
      'Banana overnight oats',
      'Paneer rice box',
    ])
  })
})
