import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'

import { fakeApi } from '../test/helpers'
import { FoodSearch } from './FoodSearch'

it('says a blank name needs typing, and puts the focus in the field, without searching', async () => {
  const calls = fakeApi({})
  const user = userEvent.setup()
  render(<FoodSearch initial="" onChoose={vi.fn<(option: unknown) => void>()} />)
  const field = screen.getByLabelText('Food name')
  // The field has the focus on opening; the person moves on to the button.
  await user.click(screen.getByRole('button', { name: 'Search' }))

  expect(screen.getByText('Type a food name first.')).toBeInTheDocument()
  expect(field).toHaveAttribute('aria-invalid', 'true')
  expect(field).toHaveFocus()
  expect(calls).toHaveLength(0)
})
