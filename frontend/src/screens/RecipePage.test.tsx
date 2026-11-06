import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { State } from '../storage'
import { CATALOGUE, sampleState } from '../test/helpers'
import type { CatalogueState, SharedCatalogue } from '../useCatalogue'
import { RecipePage } from './RecipePage'

function catalogue(loaded: CatalogueState = { status: 'ready', rows: CATALOGUE }): SharedCatalogue {
  return { state: loaded, need: vi.fn<() => void>(), reload: vi.fn<() => void>() }
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
    render(<RecipePage state={state()} day="mon" slotId="breakfast" catalogue={catalogue()} />)

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
    render(<RecipePage state={state()} day="mon" slotId="breakfast" catalogue={catalogue()} />)

    const banana = screen.getByRole('row', { name: /^Banana/ })
    expect(within(banana).getByText(', 1½ pieces')).toHaveClass('measure')
    const curd = screen.getByRole('row', { name: /^Curd/ })
    expect(curd.querySelector('.measure')).toBeNull()
  })

  it('shows grams alone for a recipe saved before measures were shown', () => {
    const saved = state()
    saved.week!.recipes[0]!.measures = []
    render(<RecipePage state={saved} day="mon" slotId="breakfast" catalogue={catalogue()} />)

    const ingredients = screen.getByRole('table', { name: 'Ingredients for one serving' })
    const rows = within(ingredients).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual(['Banana182 g', 'Oats99 g', 'Curd50 g'])
  })

  it('names the catalogue foods a step quotes by id, once the catalogue has loaded', () => {
    const saved = state()
    saved.week!.recipes[0]!.draft.steps = ['Soak the moong_dal, then layer the oats in a jar.']
    const loaded = catalogue()
    const { rerender } = render(
      <RecipePage
        state={saved}
        day="mon"
        slotId="breakfast"
        catalogue={catalogue({ status: 'loading' })}
      />,
    )

    const steps = screen.getByRole('list', { name: 'Steps' })
    expect(steps).toHaveTextContent('Soak the moong_dal, then layer the oats in a jar.')

    rerender(<RecipePage state={saved} day="mon" slotId="breakfast" catalogue={loaded} />)

    expect(steps).toHaveTextContent('Soak the moong dal, then layer the oats in a jar.')
    // What the week holds is left as the model wrote it.
    expect(saved.week!.recipes[0]!.draft.steps[0]).toContain('moong_dal')
  })

  it("names the ingredients as the catalogue does once it has loaded, and nutrition-mcp's way before", () => {
    const rows = [{ id: 'oats', name: 'Rolled oats', category: 'grain' as const }]
    const ingredientNames = (): string[] =>
      within(screen.getByRole('table', { name: 'Ingredients for one serving' }))
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[0]!.textContent ?? '')
    const { rerender } = render(
      <RecipePage
        state={state()}
        day="mon"
        slotId="breakfast"
        catalogue={catalogue({ status: 'loading' })}
      />,
    )

    expect(ingredientNames()).toEqual(['Banana', 'Oats', 'Curd'])

    rerender(
      <RecipePage
        state={state()}
        day="mon"
        slotId="breakfast"
        catalogue={catalogue({ status: 'ready', rows })}
      />,
    )

    // A food the catalogue lacks keeps nutrition-mcp's name.
    expect(ingredientNames()).toEqual(['Banana', 'Rolled oats', 'Curd'])
  })

  it('shows a gear id in a step by its gear label', () => {
    const saved = state()
    saved.week!.recipes[0]!.draft.steps = ['Warm the oats in a pressure_cooker.']
    render(<RecipePage state={saved} day="mon" slotId="breakfast" catalogue={catalogue()} />)

    expect(screen.getByRole('list', { name: 'Steps' })).toHaveTextContent(
      'Warm the oats in a pressure cooker.',
    )
  })

  it('names the ids in the store, swap and fits lines as in the steps', () => {
    const saved = state()
    const { draft } = saved.week!.recipes[0]!
    draft.store = 'Keep the soaked moong_dal in a jar_with_lid.'
    draft.swap = 'Use moong_dal instead of oats.'
    draft.fits = 'moong_dal adds protein to the morning.'
    render(<RecipePage state={saved} day="mon" slotId="breakfast" catalogue={catalogue()} />)

    expect(screen.getByText('Store:').parentElement).toHaveTextContent(
      'Store: Keep the soaked moong dal in a jar with a lid.',
    )
    expect(screen.getByText('Swap:').parentElement).toHaveTextContent(
      'Swap: Use moong dal instead of oats.',
    )
    expect(screen.getByText('How it fits:').parentElement).toHaveTextContent(
      'How it fits: Moong dal adds protein to the morning.',
    )
    // What the week holds is left as the model wrote it.
    expect(draft.swap).toBe('Use moong_dal instead of oats.')
  })

  it('names gear and leaves catalogue ids as written before the catalogue has loaded', () => {
    const saved = state()
    saved.week!.recipes[0]!.draft.swap = 'Cook moong_dal in a rice_cooker instead.'
    render(
      <RecipePage
        state={saved}
        day="mon"
        slotId="breakfast"
        catalogue={catalogue({ status: 'loading' })}
      />,
    )

    expect(screen.getByText('Swap:').parentElement).toHaveTextContent(
      'Swap: Cook moong_dal in a rice cooker instead.',
    )
  })

  it('shows the steps as written when the catalogue could not be loaded', () => {
    const saved = state()
    saved.week!.recipes[0]!.draft.steps = ['Soak the moong_dal.']
    const failed = catalogue({ status: 'failed', message: 'the API could not be reached' })
    render(<RecipePage state={saved} day="mon" slotId="breakfast" catalogue={failed} />)

    expect(screen.getByRole('list', { name: 'Steps' })).toHaveTextContent('Soak the moong_dal.')
  })

  it('asks the shell for the catalogue when it opens', () => {
    const asked = catalogue({ status: 'idle' })
    render(<RecipePage state={state()} day="mon" slotId="breakfast" catalogue={asked} />)

    expect(asked.need).toHaveBeenCalled()
  })

  it('says so when the week has no recipe for that day and meal', () => {
    render(<RecipePage state={state()} day="tue" slotId="dinner" catalogue={catalogue()} />)

    expect(screen.getByRole('heading', { level: 1, name: 'No recipe here' })).toBeVisible()
    expect(screen.getByText(/no recipe for Tuesday's Dinner/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the week' })).toHaveAttribute('href', '#/week')
  })

  it('prints from the browser', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
    const user = userEvent.setup()
    render(<RecipePage state={state()} day="mon" slotId="lunch" catalogue={catalogue()} />)

    await user.click(screen.getByRole('button', { name: 'Print this recipe' }))

    expect(print).toHaveBeenCalledTimes(1)
  })

  it('lists every recipe of the week, each on a page of its own when printed', () => {
    const { container } = render(
      <RecipePage state={state()} day={null} slotId={null} catalogue={catalogue()} />,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Recipes for the week' })).toBeVisible()
    const recipes = container.querySelectorAll('article.recipe')
    expect([...recipes].map((article) => article.querySelector('h2')?.textContent)).toEqual([
      'Banana overnight oats',
      'Paneer rice box',
    ])
  })
})
