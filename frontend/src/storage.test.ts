import { IDBDatabase, IDBFactory } from 'fake-indexeddb'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  STORAGE_KEY,
  ValidationFailed,
  StorageUnavailable,
  createStorage,
  exportFile,
  emptyState,
  importFile,
  type State,
} from './storage'
import sample from './test/export-sample.json'
import type { Export } from './types'

// A valid export, written by the API with the test fakes (tests/unit/test_export_schemas.py
// checks that the server accepts it).
function exported(): Export {
  return structuredClone(sample) as Export
}

function stateOf(file: Export): State {
  return { rules: file.rules, week: file.week, log: file.log }
}

function fileOf(content: unknown, name = 'meal-planner.json'): File {
  const text = typeof content === 'string' ? content : JSON.stringify(content)
  return new File([text], name, { type: 'application/json' })
}

async function rejection(promise: Promise<unknown>): Promise<ValidationFailed> {
  const error: unknown = await promise.then(
    () => null,
    (reason: unknown) => reason,
  )
  expect(error).toBeInstanceOf(ValidationFailed)
  return error as ValidationFailed
}

function paths(error: ValidationFailed): string[] {
  return error.problems.map((problem) => problem.path)
}

// An IndexedDB that cannot be opened, as in a browser that blocks site storage.
const throwingIndexedDB = {
  open(): never {
    throw new DOMException('The operation is insecure.', 'SecurityError')
  },
} as unknown as IDBFactory

// An IndexedDB whose open request fails later, as Firefox's private windows used to.
const failingIndexedDB = {
  open(): IDBOpenDBRequest {
    const request = { error: new DOMException('denied', 'InvalidStateError') } as {
      error: DOMException
      onerror?: (event: Event) => void
    }
    queueMicrotask(() => request.onerror?.(new Event('error')))
    return request as unknown as IDBOpenDBRequest
  },
} as unknown as IDBFactory

function throwingLocalStorage(): Storage {
  const fail = (): never => {
    throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
  }
  return {
    length: 0,
    clear: fail,
    getItem: fail,
    key: fail,
    removeItem: fail,
    setItem: fail,
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe('import', () => {
  it('rejects version 2', async () => {
    const file = { ...exported(), version: 2 }

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/version'])
    expect(error.problems[0]?.message).toBe('must be 1')
  })

  it('rejects unknown food id field type with its path', async () => {
    const file = exported()
    const entry = file.log.entries[0]!
    ;(entry.items[0] as unknown as Record<string, unknown>).food_id = 42

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/0/items/0/food_id'])
    expect(error.problems[0]?.message).toBe('must be a string')
    expect(error.message).toContain('/log/entries/0/items/0/food_id')
  })

  it('rejects a field the file does not have, at that field', async () => {
    const file = { ...exported(), notes: 'hello' }

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/notes'])
  })

  it('rejects a missing field at that field', async () => {
    const { log: _log, ...file } = exported()

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log'])
    expect(error.problems[0]?.message).toBe('is missing')
  })

  it('names the field inside the week, not only the week', async () => {
    const file = exported()
    ;(file.week!.recipes[0]!.draft as unknown as Record<string, unknown>).title = ''

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/week/recipes/0/draft/title'])
  })

  it('lists a problem of a list and a problem inside it together', async () => {
    const file = exported()
    const entry = file.log.entries[0]!
    entry.items = Array.from({ length: 21 }, () => ({
      food_id: 'banana',
      amount: 1,
      unit: 'piece',
    }))
    entry.items[3]!.amount = -1

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/0/items', '/log/entries/0/items/3/amount'])
  })

  it('says what a value that may be empty must be, without the empty part', async () => {
    const file = exported()
    ;(file.week as unknown as Record<string, unknown>).cost_usd = 'free'

    const error = await rejection(importFile(fileOf(file)))

    expect(error.problems).toEqual([{ path: '/week/cost_usd', message: 'must be a number' }])
  })

  it('rejects a file that is not JSON', async () => {
    const error = await rejection(importFile(fileOf('{"version": 1,')))

    expect(paths(error)).toEqual([''])
    expect(error.problems[0]?.message).toMatch(/not JSON/)
  })

  it('rejects a date that is not on the calendar', async () => {
    const file = exported()
    file.log.entries[0]!.date = '2026-02-30'

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/0/date'])
  })

  it('accepts a time without seconds, as the server does', async () => {
    const file = exported()
    file.rules.slots[0]!.eat_at = '07:45'

    await expect(importFile(fileOf(file))).resolves.toMatchObject({ rules: file.rules })
  })

  it('fills in a field the server gives a default', async () => {
    const file = exported()
    delete (file.week!.recipes[0] as unknown as Record<string, unknown>).portion_scale

    const state = await importFile(fileOf(file))

    expect(state.week!.recipes[0]!.portion_scale).toBe(1)
  })
})

describe('import checks what the schema cannot say', () => {
  it('rejects a skipped meal that lists foods', async () => {
    const file = exported()
    file.log.entries[0]!.status = 'skipped'

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/0/items'])
    expect(error.problems[0]?.message).toBe('2026-10-26 breakfast: a skipped meal lists no foods')
  })

  it('rejects an eaten meal that lists none', async () => {
    const file = exported()
    file.log.entries[1]!.items = []

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/1/items'])
  })

  it('rejects a meal logged twice for one date and slot', async () => {
    const file = exported()
    file.log.entries.push(structuredClone(file.log.entries[0]!))

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/3'])
    expect(error.problems[0]?.message).toBe('2026-10-26 breakfast is listed more than once')
  })

  it('rejects a log longer than a week', async () => {
    const file = exported()
    file.log.entries[2]!.date = '2026-11-02'

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries'])
    expect(error.problems[0]?.message).toContain('8 days')
  })

  it('rejects a log entry for a slot the plan does not have', async () => {
    const file = exported()
    file.log.entries[1]!.slot_id = 'brunch'

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/log/entries/1/slot_id'])
  })

  it('rejects a week naming a day type the plan does not define', async () => {
    const file = exported()
    file.rules.week.sun = 'holiday'

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/rules/week/sun'])
  })

  it('rejects two slots with one id', async () => {
    const file = exported()
    file.rules.slots[1]!.id = file.rules.slots[0]!.id

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toContain('/rules/slots/1/id')
  })

  it('rejects slot shares that do not add up to a day', async () => {
    const file = exported()
    file.rules.slots[0]!.kcal_share = 0.2

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/rules/slots'])
    expect(error.problems[0]?.message).toContain('kcal_share')
  })

  it('rejects an extra target that repeats a target of the day type', async () => {
    const file = exported()
    file.rules.day_types[0]!.extra.push({ nutrient: 'protein_g', kind: 'min', value: 100 })

    const error = await rejection(importFile(fileOf(file)))

    expect(paths(error)).toEqual(['/rules/day_types/0/extra/2/nutrient'])
  })
})

describe('export', () => {
  it('export then import round-trips', async () => {
    const state = stateOf(exported())

    const file = new File([await exportFile(state).text()], 'meal-planner.json')

    await expect(importFile(file)).resolves.toEqual(state)
  })

  it('writes the export file format', async () => {
    const state = stateOf(exported())

    const blob = exportFile(state)

    expect(blob.type).toBe('application/json')
    expect(JSON.parse(await blob.text())).toEqual({ version: 1, ...state })
  })

  it('has nothing to export before the plan rules are saved', () => {
    expect(() => exportFile(emptyState())).toThrow(/plan rules/)
  })
})

describe('storage', () => {
  it('starts empty', async () => {
    const storage = createStorage({ indexedDB: new IDBFactory(), localStorage })

    await expect(storage.loadState()).resolves.toEqual(emptyState())
  })

  it('keeps the state in IndexedDB', async () => {
    const indexedDB = new IDBFactory()
    const state = stateOf(exported())

    await createStorage({ indexedDB, localStorage }).saveState(state)

    // A new page load reads it back from the same database.
    await expect(createStorage({ indexedDB, localStorage }).loadState()).resolves.toEqual(state)
    expect(localStorage.length).toBe(0)
  })

  it('storage falls back to localStorage when IndexedDB throws', async () => {
    const state = stateOf(exported())
    const storage = createStorage({ indexedDB: throwingIndexedDB, localStorage })

    await storage.saveState(state)

    expect(localStorage.length).toBe(1)
    await expect(storage.loadState()).resolves.toEqual(state)
    const reloaded = createStorage({ indexedDB: throwingIndexedDB, localStorage })
    await expect(reloaded.loadState()).resolves.toEqual(state)
  })

  it('falls back to localStorage when IndexedDB fails to open', async () => {
    const state = stateOf(exported())

    await createStorage({ indexedDB: failingIndexedDB, localStorage }).saveState(state)

    const reloaded = createStorage({ indexedDB: failingIndexedDB, localStorage })
    await expect(reloaded.loadState()).resolves.toEqual(state)
  })

  it('falls back to localStorage when the browser has no IndexedDB', async () => {
    const state = stateOf(exported())

    await createStorage({ indexedDB: undefined, localStorage }).saveState(state)

    await expect(
      createStorage({ indexedDB: undefined, localStorage }).loadState(),
    ).resolves.toEqual(state)
  })

  it('reads the localStorage copy when IndexedDB works again, since it is newer', async () => {
    const indexedDB = new IDBFactory()
    const older = stateOf(exported())
    const newer: State = { ...older, week: null }
    await createStorage({ indexedDB, localStorage }).saveState(older)
    await createStorage({ indexedDB: throwingIndexedDB, localStorage }).saveState(newer)

    const storage = createStorage({ indexedDB, localStorage })

    await expect(storage.loadState()).resolves.toEqual(newer)
    // The next save goes to IndexedDB and clears the copy, so the two cannot disagree.
    await storage.saveState(newer)
    expect(localStorage.length).toBe(0)
    await expect(createStorage({ indexedDB, localStorage }).loadState()).resolves.toEqual(newer)
  })

  it('says so when what IndexedDB holds cannot be read, instead of starting empty', async () => {
    const indexedDB = new IDBFactory()
    await createStorage({ indexedDB, localStorage }).saveState(stateOf(exported()))
    vi.spyOn(IDBDatabase.prototype, 'transaction').mockImplementation(() => {
      throw new DOMException('The database connection is closing.', 'InvalidStateError')
    })

    // An empty state here would let the next save write over the plan that is still there.
    await expect(createStorage({ indexedDB, localStorage }).loadState()).rejects.toMatchObject({
      name: 'InvalidStateError',
    })
  })

  it('gives a new empty state each time', () => {
    const first = emptyState()
    first.log.entries.push(exported().log.entries[0]!)

    expect(emptyState().log.entries).toEqual([])
  })

  it('says so when the browser keeps nothing', async () => {
    const storage = createStorage({
      indexedDB: throwingIndexedDB,
      localStorage: throwingLocalStorage(),
    })

    await expect(storage.saveState(stateOf(exported()))).rejects.toBeInstanceOf(StorageUnavailable)
    await expect(storage.loadState()).resolves.toEqual(emptyState())
  })

  it('refuses saved data that no longer fits the file format, with its path', async () => {
    const indexedDB = new IDBFactory()
    const state = stateOf(exported())
    const storage = createStorage({ indexedDB, localStorage })
    await storage.saveState(state)
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: 1, ...state, log: { version: 1, entries: 'none' } }),
    )

    const error = await rejection(createStorage({ indexedDB, localStorage }).loadState())

    expect(paths(error)).toEqual(['/log/entries'])
  })

  it('keeps a state with no plan yet', async () => {
    const indexedDB = new IDBFactory()
    const state: State = { ...emptyState(), log: exported().log }

    await createStorage({ indexedDB, localStorage }).saveState(state)

    await expect(createStorage({ indexedDB, localStorage }).loadState()).resolves.toEqual(state)
  })
})
