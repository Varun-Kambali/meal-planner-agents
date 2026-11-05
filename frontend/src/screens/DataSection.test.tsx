import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'

import type { SaveOutcome } from '../session'
import { ValidationFailed, emptyState, type State } from '../storage'
import { sampleExport } from '../test/helpers'
import { DataSection } from './DataSection'

it('says a file was not loaded when its state is refused, and not that it was', async () => {
  const refused = new ValidationFailed([
    { path: '/log/entries', message: 'must list at most 7 days' },
  ])
  const onReplace = vi.fn<(state: State) => Promise<SaveOutcome>>(async () => ({
    ok: false,
    error: refused,
  }))
  const user = userEvent.setup()
  render(<DataSection state={emptyState()} onReplace={onReplace} blocked={null} />)
  const file = new File([JSON.stringify(sampleExport())], 'coach-plan.json', {
    type: 'application/json',
  })

  await user.upload(screen.getByLabelText('Load a file'), file)

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'coach-plan.json was not loaded: the data does not fit the file format: /log/entries must list at most 7 days.',
  )
  expect(screen.queryByText(/Loaded coach-plan.json/)).not.toBeInTheDocument()
})
