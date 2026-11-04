import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { State } from '../storage'
import { sampleState } from '../test/helpers'
import { RecipePage } from './RecipePage'

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
    render(<RecipePage state={state()} day="mon" slotId="breakfast" />)

    expect(screen.getByRole('heading', { level: 1, name: 'Banana overnight oats' })).toBeVisible()
    expect(screen.getByText(/Monday, Breakfast at 08:00/)).toHaveTextContent('South Indian')
    const status = screen.getByText('Misses:').closest('section')!
    expect(status).toHaveTextContent('calories 540, the meal needs 596 to 729')

    const ingredients = screen.getByRole('table', { name: 'Ingredients for one serving' })
    const rows = within(ingredients).getAllByRole('row').slice(1)
    expect(rows.map((row) => row.textContent)).toEqual(['Banana182 g', 'Oats99 g', 'Curd50 g'])
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

  it('says so when the week has no recipe for that day and meal', () => {
    render(<RecipePage state={state()} day="tue" slotId="dinner" />)

    expect(screen.getByRole('heading', { level: 1, name: 'No recipe here' })).toBeVisible()
    expect(screen.getByText(/no recipe for Tuesday's Dinner/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the week' })).toHaveAttribute('href', '#/week')
  })

  it('prints from the browser', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
    const user = userEvent.setup()
    render(<RecipePage state={state()} day="mon" slotId="lunch" />)

    await user.click(screen.getByRole('button', { name: 'Print this recipe' }))

    expect(print).toHaveBeenCalledTimes(1)
  })

  it('lists every recipe of the week, each on a page of its own when printed', () => {
    const { container } = render(<RecipePage state={state()} day={null} slotId={null} />)

    expect(screen.getByRole('heading', { level: 1, name: 'Recipes for the week' })).toBeVisible()
    const recipes = container.querySelectorAll('article.recipe')
    expect([...recipes].map((article) => article.querySelector('h2')?.textContent)).toEqual([
      'Banana overnight oats',
      'Paneer rice box',
    ])
  })
})
