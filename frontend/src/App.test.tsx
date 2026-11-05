import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import {
  STORAGE_KEY,
  SavedDataUnreadable,
  StorageUnavailable,
  emptyState,
  type State,
  type StateStore,
} from './storage'
import {
  CATALOGUE,
  FakeStream,
  catchDownloads,
  fakeApi,
  json,
  sampleExport,
  sampleState,
} from './test/helpers'

function seed(state: State): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...state }))
}

// A store over a state held in memory, whose calls a test can make fail.
function memoryStore(state: State, changes: Partial<StateStore> = {}): StateStore {
  let kept: State = state
  return {
    loadState: async () => kept,
    saveState: async (next) => {
      kept = next
    },
    readStoredText: async () => JSON.stringify({ version: 1, ...kept }),
    discardStoredText: async () => {
      kept = emptyState()
    },
    ...changes,
  }
}

beforeEach(() => {
  window.location.hash = ''
})

afterEach(() => {
  localStorage.clear()
  window.location.hash = ''
})

describe('the shell', () => {
  it('opens on the plan form when no plan is saved, with the screens in the navigation', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })

    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: 'Your plan' })).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Screens' })
    expect(within(nav).getByRole('link', { name: 'Plan' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'Week' })).toHaveAttribute('href', '#/week')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
    expect(screen.getByRole('region', { name: 'Your data' })).toHaveTextContent(
      'saved in this browser only',
    )
  })

  it('opens on the week when a plan is saved, and moves to the plan form from the navigation', async () => {
    seed(sampleState())
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { level: 1, name: 'The week' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Plan' }))

    const heading = await screen.findByRole('heading', { level: 1, name: 'Your plan' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(document.title).toBe('Your plan: Meal planner')
  })

  it('shows the field the API refused in the plan form after writing the week', async () => {
    seed({ ...sampleState(), week: null })
    fakeApi({
      '/v1/catalogue': () => json(CATALOGUE),
      '/v1/week': () =>
        json(
          {
            detail: [
              { loc: ['body', 'food', 'liked_foods'], msg: "'paneer' is not in the catalogue" },
            ],
          },
          422,
        ),
    })
    const user = userEvent.setup()
    window.location.hash = '#/week'
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write the week' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("Foods you like: 'paneer' is not in the catalogue")
    await user.click(within(alert).getByRole('link', { name: 'Fix them in your plan' }))

    const liked = await screen.findByLabelText('Foods you like')
    expect(liked).toHaveAttribute('aria-invalid', 'true')
    expect(liked).toHaveAccessibleDescription(
      expect.stringContaining("'paneer' is not in the catalogue"),
    )
  })

  it('says a change was not saved and offers the export, when the browser keeps nothing', async () => {
    const downloads = catchDownloads()
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const store = memoryStore(
      { ...sampleState(), week: null },
      {
        saveState: async () => {
          throw new StorageUnavailable()
        },
      },
    )
    const user = userEvent.setup()
    window.location.hash = '#/plan'
    render(<App store={store} />)

    await user.click(await screen.findByRole('button', { name: 'Save the plan' }))

    const alert = await screen.findByRole('alert', { name: 'Not saved in this browser' })
    expect(alert).toHaveTextContent('this browser does not let the page keep data')
    await user.click(within(alert).getByRole('button', { name: 'Export a copy' }))
    expect(downloads.map((file) => file.name)).toEqual([
      expect.stringMatching(/^meal-planner-\d{4}-\d{2}-\d{2}\.json$/),
    ])
  })
})

describe('export and import', () => {
  it('exports the saved state as one file', async () => {
    seed(sampleState())
    const downloads = catchDownloads()
    const user = userEvent.setup()
    window.location.hash = '#/week'
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Export a copy' }))

    expect(downloads).toHaveLength(1)
    expect(JSON.parse(await downloads[0]!.text())).toEqual(sampleExport())
  })

  it('says so when the browser will not make the file', async () => {
    seed(sampleState())
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      throw new DOMException('Downloads are blocked.', 'SecurityError')
    })
    const user = userEvent.setup()
    window.location.hash = '#/week'
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Export a copy' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The copy could not be made: Downloads are blocked.',
    )
  })

  it('asks before a file replaces a saved plan, then loads it', async () => {
    seed({ ...sampleState(), week: null, log: { version: 1, entries: [] } })
    const user = userEvent.setup()
    window.location.hash = '#/week'
    render(<App />)
    const file = new File([JSON.stringify(sampleExport())], 'coach-plan.json', {
      type: 'application/json',
    })

    await user.upload(await screen.findByLabelText('Load a file'), file)
    const question = await screen.findByRole('region', { name: 'Replace what this browser saved?' })
    expect(question).toHaveTextContent(
      'coach-plan.json has the plan Office weekday, with 2 recipes',
    )
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).week).toBeNull()
    await user.click(within(question).getByRole('button', { name: 'Replace with the file' }))

    expect(await screen.findByText('Loaded coach-plan.json.')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).week).toEqual(sampleExport().week)
  })

  it('loads no file while a week is being written, so the run cannot save over it', async () => {
    seed({ ...sampleState(), week: null })
    const run = new FakeStream()
    fakeApi({ '/v1/week': () => run.response })
    const user = userEvent.setup()
    window.location.hash = '#/week'
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Write the week' }))
    const input = await screen.findByLabelText('Load a file')
    expect(input).toBeDisabled()
    expect(input).toHaveAccessibleDescription(
      expect.stringContaining('A file can be loaded once the week being written is done.'),
    )
    run.send({ kind: 'week_done', week: sampleExport().week! })
    run.end()

    await waitFor(() => expect(screen.getByLabelText('Load a file')).toBeEnabled())
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).week).toEqual(sampleExport().week)
  })

  it('lists where a file does not fit, and loads nothing', async () => {
    seed(sampleState())
    const user = userEvent.setup()
    window.location.hash = '#/week'
    render(<App />)
    const bad = { ...sampleExport(), version: 2 }

    await user.upload(
      await screen.findByLabelText('Load a file'),
      new File([JSON.stringify(bad)], 'old.json', { type: 'application/json' }),
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('old.json does not fit the file format, so nothing was loaded.')
    expect(alert).toHaveTextContent('/version Must be 1')
    expect(screen.queryByRole('region', { name: /Replace/ })).not.toBeInTheDocument()
  })
})

describe('saved data the page cannot open', () => {
  it('says why, saves the stored copy as a file, and only then offers to remove it', async () => {
    localStorage.setItem(STORAGE_KEY, '{"version": 1, "rules": "not a plan"}')
    const downloads = catchDownloads()
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const user = userEvent.setup()
    render(<App />)

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your saved plan could not be opened' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveTextContent('does not fit the file format')
    expect(screen.getByRole('main')).toHaveTextContent('/rules')
    expect(screen.queryByRole('button', { name: /Remove the stored copy/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save the stored copy as a file' }))
    expect(downloads).toHaveLength(1)
    expect(await downloads[0]!.text()).toBe('{"version": 1, "rules": "not a plan"}')
    // Nothing was written over it.
    expect(localStorage.getItem(STORAGE_KEY)).toBe('{"version": 1, "rules": "not a plan"}')

    await user.click(await screen.findByRole('button', { name: 'Remove the stored copy' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Your plan' })).toBeInTheDocument()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('says to try again when IndexedDB cannot be read, and does not offer to remove anything', async () => {
    const unreadable = new SavedDataUnreadable(new Error('closed'))
    let opens = 0
    const store = memoryStore(sampleState(), {
      loadState: async () => {
        opens += 1
        if (opens === 1) throw unreadable
        return sampleState()
      },
      readStoredText: async () => {
        throw unreadable
      },
    })
    catchDownloads()
    const user = userEvent.setup()
    render(<App store={store} />)

    const main = await screen.findByRole('main')
    await waitFor(() => expect(main).toHaveTextContent('cannot be read right now'))
    await user.click(screen.getByRole('button', { name: 'Save the stored copy as a file' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The stored copy cannot be read either',
    )
    expect(screen.queryByRole('button', { name: /Remove the stored copy/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'The week' })).toBeInTheDocument()
  })

  it('says so when removing the stored copy fails, and keeps the screen', async () => {
    const store = memoryStore(sampleState(), {
      loadState: async () => {
        throw new SavedDataUnreadable(new Error('closed'))
      },
      readStoredText: async () => '{"version": 1}',
      discardStoredText: async () => {
        throw new SavedDataUnreadable(new Error('closed'))
      },
    })
    catchDownloads()
    const user = userEvent.setup()
    render(<App store={store} />)

    await user.click(await screen.findByRole('button', { name: 'Save the stored copy as a file' }))
    await user.click(await screen.findByRole('button', { name: 'Remove the stored copy' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The stored copy was not removed: what this browser saved cannot be read right now',
    )
    expect(
      screen.getByRole('heading', { level: 1, name: 'Your saved plan could not be opened' }),
    ).toBeInTheDocument()
  })
})
