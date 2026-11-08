import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'

import officeWeekday from '../../../tests/fixtures/plans/office_weekday.json?raw'
import type { SaveOutcome } from '../session'
import { ValidationFailed, emptyState, loadFile, type State } from '../storage'
import { catchDownloads, sampleExport, sampleState } from '../test/helpers'
import type { PlanRules } from '../types'
import { DataSection } from './DataSection'

function saver() {
  return vi.fn<(state: State) => Promise<SaveOutcome>>(async () => ({ ok: true }))
}

function fileOf(content: unknown, name: string): File {
  const text = typeof content === 'string' ? content : JSON.stringify(content)
  return new File([text], name, { type: 'application/json' })
}

// The coach's plan without the snack: its calories and protein go to dinner.
function withoutSnack(): PlanRules {
  const rules = JSON.parse(officeWeekday) as PlanRules
  rules.slots = rules.slots.filter((slot) => slot.id !== 'snack')
  const dinner = rules.slots.find((slot) => slot.id === 'dinner')!
  dinner.kcal_share = 0.45
  dinner.protein_share = 0.45
  return rules
}

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

it('loads a plan-rules file as rules only: an empty week, and the log kept when it fits', async () => {
  const onReplace = saver()
  const user = userEvent.setup()
  const state = sampleState()
  render(<DataSection state={state} onReplace={onReplace} blocked={null} />)

  await user.upload(
    screen.getByLabelText('Load a file'),
    fileOf(officeWeekday, 'office_weekday.json'),
  )

  const question = await screen.findByRole('region', { name: 'Replace what this browser saved?' })
  expect(question).toHaveTextContent('only the rules of the plan Office weekday')
  expect(question).toHaveTextContent('The week will be empty')
  expect(question).toHaveTextContent('The meal log is kept')
  expect(onReplace).not.toHaveBeenCalled()
  await user.click(within(question).getByRole('button', { name: 'Replace with the file' }))

  expect(onReplace).toHaveBeenCalledTimes(1)
  const loaded = onReplace.mock.calls[0]![0]
  expect(loaded.rules).toEqual(JSON.parse(officeWeekday))
  expect(loaded.week).toBeNull()
  expect(loaded.log).toEqual(state.log)
  expect(await screen.findByText('Loaded office_weekday.json.')).toBeInTheDocument()
})

it('asks before it removes log entries for meals a rules file lacks, and lets the person export first', async () => {
  const files = catchDownloads()
  const onReplace = saver()
  const user = userEvent.setup()
  const state = sampleState()
  render(<DataSection state={state} onReplace={onReplace} blocked={null} />)

  await user.upload(screen.getByLabelText('Load a file'), fileOf(withoutSnack(), 'no-snack.json'))

  const question = await screen.findByRole('region', { name: 'Replace what this browser saved?' })
  expect(question).toHaveTextContent('1 logged meal for Evening snack will be removed')
  expect(onReplace).not.toHaveBeenCalled()
  await user.click(within(question).getByRole('button', { name: 'Export a copy first' }))
  expect(files).toHaveLength(1)
  const exported = JSON.parse(await files[0]!.text()) as State
  expect(exported.log.entries).toHaveLength(3)
  await user.click(within(question).getByRole('button', { name: 'Replace with the file' }))

  const loaded = onReplace.mock.calls[0]![0]
  expect(loaded.log.entries.map((entry) => entry.slot_id)).toEqual(['breakfast', 'lunch'])
  expect(loaded.week).toBeNull()
})

it('loads a rules file with nothing to ask when nothing is saved', async () => {
  const onReplace = saver()
  const user = userEvent.setup()
  render(<DataSection state={emptyState()} onReplace={onReplace} blocked={null} />)

  await user.upload(
    screen.getByLabelText('Load a file'),
    fileOf(officeWeekday, 'office_weekday.json'),
  )

  expect(await screen.findByText('Loaded office_weekday.json.')).toBeInTheDocument()
  expect(onReplace.mock.calls[0]![0].rules!.name).toBe('Office weekday')
})

it('still refuses a file that fits neither format, naming where', async () => {
  const onReplace = saver()
  const user = userEvent.setup()
  render(<DataSection state={sampleState()} onReplace={onReplace} blocked={null} />)

  await user.upload(
    screen.getByLabelText('Load a file'),
    fileOf({ ...sampleExport(), version: 2 }, 'old.json'),
  )

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('old.json does not fit the file format, so nothing was loaded.')
  expect(alert).toHaveTextContent('/version')
  expect(onReplace).not.toHaveBeenCalled()
})

it('refuses a rules file that breaks a rule the form checks, naming where', async () => {
  const rules = JSON.parse(officeWeekday) as PlanRules
  rules.slots[0]!.kcal_share = 0.5
  const user = userEvent.setup()
  render(<DataSection state={sampleState()} onReplace={saver()} blocked={null} />)

  await user.upload(screen.getByLabelText('Load a file'), fileOf(rules, 'shares.json'))

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('shares.json does not fit the file format')
  expect(alert).toHaveTextContent('/slots')
})

it('exports the rules alone as a file the page loads again as rules', async () => {
  const files = catchDownloads()
  const user = userEvent.setup()
  const state = sampleState()
  render(<DataSection state={state} onReplace={saver()} blocked={null} />)

  await user.click(screen.getByRole('button', { name: 'Export the rules only' }))

  expect(files).toHaveLength(1)
  expect(files[0]!.name).toMatch(/^meal-planner-rules-\d{4}-\d{2}-\d{2}\.json$/)
  const loaded = await loadFile(new Blob([await files[0]!.text()]))
  expect(loaded).toEqual({ kind: 'rules', rules: state.rules })
})

it('says so when the rules file could not be made', async () => {
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
    throw new Error('Downloads are blocked')
  })
  const user = userEvent.setup()
  render(<DataSection state={sampleState()} onReplace={saver()} blocked={null} />)

  await user.click(screen.getByRole('button', { name: 'Export the rules only' }))

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'The copy could not be made: Downloads are blocked.',
  )
  expect(screen.queryByText(/Exported meal-planner-rules/)).not.toBeInTheDocument()
})

it('says what is sent to the model, and that the server stores nothing', () => {
  render(<DataSection state={sampleState()} onReplace={saver()} blocked={null} />)

  const notice = screen.getByRole('region', { name: 'Your data' })
  expect(notice).toHaveTextContent('are sent to the model')
  expect(notice).toHaveTextContent('Gemini API')
  expect(notice).toHaveTextContent('stores nothing')
})
