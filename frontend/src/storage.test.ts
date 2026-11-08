import { IDBDatabase, IDBFactory } from 'fake-indexeddb'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  STORAGE_KEY,
  SavedDataUnreadable,
  ValidationFailed,
  StorageUnavailable,
  createStorage,
  exportFile,
  emptyState,
  importFile,
  loadFile,
  type State,
} from './storage'
import officeWeekday from '../../tests/fixtures/plans/office_weekday.json?raw'
import sample from './test/export-sample.json'
import invalidExports from './test/invalid-exports.json'
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

describe('loading a file', () => {
  it('takes a plan-rules file as rules only', async () => {
    const loaded = await loadFile(fileOf(officeWeekday, 'office_weekday.json'))

    expect(loaded.kind).toBe('rules')
    expect(loaded.kind === 'rules' && loaded.rules.name).toBe('Office weekday')
    expect(loaded.kind === 'rules' && loaded.rules.slots.map((slot) => slot.id)).toEqual([
      'breakfast',
      'lunch',
      'snack',
      'dinner',
    ])
  })

  it('takes an export as the whole state, as before', async () => {
    const loaded = await loadFile(fileOf(exported()))

    expect(loaded).toEqual({ kind: 'export', state: stateOf(exported()) })
  })

  it('runs the checks the form runs on the rules of a rules file', async () => {
    const rules = JSON.parse(officeWeekday) as { slots: { kcal_share: number }[] }
    rules.slots[0]!.kcal_share = 0.5

    const error = await rejection(loadFile(fileOf(rules)))

    expect(paths(error)).toEqual(['/slots'])
    expect(error.problems[0]?.message).toMatch(/kcal_share values must add up to 1/)
  })

  it('refuses a rules file with a blank name, with its path in the file', async () => {
    const rules = { ...(JSON.parse(officeWeekday) as object), name: '   ' }

    const error = await rejection(loadFile(fileOf(rules)))

    expect(paths(error)).toEqual(['/name'])
  })

  it('refuses a file that fits neither format, with the export format errors', async () => {
    const error = await rejection(loadFile(fileOf({ hello: 'world' })))

    expect(paths(error).sort()).toEqual(['/hello', '/log', '/rules', '/version', '/week'])
  })

  it('refuses a file that is not JSON', async () => {
    const error = await rejection(loadFile(fileOf('not json')))

    expect(error.problems[0]?.message).toMatch(/^is not JSON/)
  })
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

  it('counts the length of a name as the server does, an emoji as one character', async () => {
    const file = exported()
    file.rules.name = '\u{1F35B}'.repeat(60)

    await expect(importFile(fileOf(file))).resolves.toMatchObject({
      rules: { name: file.rules.name },
    })
    file.rules.name += '\u{1F35B}'
    const error = await rejection(importFile(fileOf(file)))
    expect(error.problems).toEqual([
      { path: '/rules/name', message: 'must have at most 60 characters' },
    ])
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

  it('loads a file saved before household measures were shown, with none', async () => {
    const file = exported()
    for (const recipe of file.week!.recipes) {
      delete (recipe as unknown as Record<string, unknown>).measures
    }

    const state = await importFile(fileOf(file))

    expect(state.week!.recipes.map((recipe) => recipe.measures)).toEqual([[], []])
  })
})

// The files in test/invalid-exports.json, each the sample with a change or two. The server's
// tests (tests/unit/test_export_schemas.py) check that it refuses each one too, so the browser's
// copy of the server's rules cannot drift from them for these cases without a suite failing.
interface InvalidExport {
  case: string
  changes: { at: string; value?: unknown; copy?: string }[]
  path: string
  refused_by: 'model' | 'summary'
}

function at(data: unknown, pointer: string): unknown {
  return pointer
    .split('/')
    .slice(1)
    .reduce<unknown>((value, key) => (value as Record<string, unknown>)[key], data)
}

function withChanges(cases: InvalidExport): unknown {
  const file = exported() as unknown
  for (const change of cases.changes) {
    const keys = change.at.split('/').slice(1)
    const last = keys.pop()!
    const parent = at(file, keys.length === 0 ? '' : `/${keys.join('/')}`) as Record<
      string,
      unknown
    >
    const value = change.copy === undefined ? change.value : structuredClone(at(file, change.copy))
    if (Array.isArray(parent) && last === '-') parent.push(value)
    else parent[last] = value
  }
  return file
}

describe('import refuses every file the server refuses', () => {
  it.each(invalidExports as InvalidExport[])('$case, at $path', async (cases) => {
    const error = await rejection(importFile(fileOf(withChanges(cases))))

    expect(paths(error)).toContain(cases.path)
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

  // An open request that fails may hide a plan saved earlier (iOS Safari loses its connection
  // to the database server, say), unlike an open that throws at once, which means none was.
  it('does not start empty when IndexedDB fails to open, since it may hold a plan', async () => {
    const storage = createStorage({ indexedDB: failingIndexedDB, localStorage })

    await expect(storage.loadState()).rejects.toBeInstanceOf(SavedDataUnreadable)
  })

  it('does not save over a plan IndexedDB may hold when it fails to open', async () => {
    const storage = createStorage({ indexedDB: failingIndexedDB, localStorage })

    await expect(storage.saveState(stateOf(exported()))).rejects.toBeInstanceOf(SavedDataUnreadable)
    expect(localStorage.length).toBe(0)
  })

  it('keeps using the localStorage copy when IndexedDB fails to open, since it is newer', async () => {
    const state = stateOf(exported())
    await createStorage({ indexedDB: throwingIndexedDB, localStorage }).saveState(state)
    const storage = createStorage({ indexedDB: failingIndexedDB, localStorage })

    await expect(storage.loadState()).resolves.toEqual(state)
    const changed: State = { ...state, week: null }
    await storage.saveState(changed)
    await expect(storage.loadState()).resolves.toEqual(changed)
  })

  it('tries IndexedDB again after it failed to open', async () => {
    const real = new IDBFactory()
    const state = stateOf(exported())
    await createStorage({ indexedDB: real, localStorage }).saveState(state)
    let opens = 0
    const flaky = {
      open: (name: string, version?: number) =>
        opens++ === 0 ? failingIndexedDB.open(name, version) : real.open(name, version),
    } as unknown as IDBFactory
    const storage = createStorage({ indexedDB: flaky, localStorage })

    await expect(storage.loadState()).rejects.toBeInstanceOf(SavedDataUnreadable)
    await expect(storage.loadState()).resolves.toEqual(state)
  })

  it('stops writing to a connection another tab has closed to upgrade the database', async () => {
    const indexedDB = new IDBFactory()
    const state = stateOf(exported())
    const storage = createStorage({ indexedDB, localStorage })
    await storage.saveState(state)
    // A newer version of the page, in another tab, upgrades the database.
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open('meal-planner', 2)
      request.onsuccess = () => {
        request.result.close()
        resolve()
      }
      request.onerror = () => reject(request.error)
    })

    // This tab's version cannot open the upgraded database, and must not hide that by saving
    // a copy to localStorage that the newer tab would never read.
    await expect(storage.saveState({ ...state, week: null })).rejects.toBeInstanceOf(
      SavedDataUnreadable,
    )
    expect(localStorage.length).toBe(0)
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
    await expect(createStorage({ indexedDB, localStorage }).loadState()).rejects.toBeInstanceOf(
      SavedDataUnreadable,
    )
  })

  it('refuses to save a state that would not load again, and keeps the last good one', async () => {
    const indexedDB = new IDBFactory()
    const good = stateOf(exported())
    const storage = createStorage({ indexedDB, localStorage })
    await storage.saveState(good)
    const bad = stateOf(exported())
    bad.log.entries[2]!.date = '2026-11-02'

    const error = await rejection(storage.saveState(bad))

    expect(paths(error)).toEqual(['/log/entries'])
    await expect(createStorage({ indexedDB, localStorage }).loadState()).resolves.toEqual(good)
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

// What the page offers when the saved state cannot be opened: the stored text as it is, to save
// as a file, and then a way to remove it.
describe('the stored copy', () => {
  it('reads the localStorage copy as it is, even when it does not fit', async () => {
    const indexedDB = new IDBFactory()
    await createStorage({ indexedDB, localStorage }).saveState(stateOf(exported()))
    localStorage.setItem(STORAGE_KEY, '{"version": 1, "rules": "broken"')

    const storage = createStorage({ indexedDB, localStorage })

    await expect(storage.readStoredText()).resolves.toBe('{"version": 1, "rules": "broken"')
  })

  it('reads what IndexedDB holds when there is no localStorage copy', async () => {
    const indexedDB = new IDBFactory()
    const state = stateOf(exported())
    await createStorage({ indexedDB, localStorage }).saveState(state)

    const text = await createStorage({ indexedDB, localStorage }).readStoredText()

    expect(JSON.parse(text!)).toEqual({ version: 1, ...state })
  })

  it('has no stored copy before anything is saved', async () => {
    const storage = createStorage({ indexedDB: new IDBFactory(), localStorage })

    await expect(storage.readStoredText()).resolves.toBeNull()
  })

  it('says so when IndexedDB cannot be opened to read it', async () => {
    const storage = createStorage({ indexedDB: failingIndexedDB, localStorage })

    await expect(storage.readStoredText()).rejects.toBeInstanceOf(SavedDataUnreadable)
  })

  it('removes the localStorage copy, so the older IndexedDB state opens', async () => {
    const indexedDB = new IDBFactory()
    const older = stateOf(exported())
    await createStorage({ indexedDB, localStorage }).saveState(older)
    localStorage.setItem(STORAGE_KEY, 'not json')
    const storage = createStorage({ indexedDB, localStorage })

    await storage.discardStoredText()

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    await expect(storage.loadState()).resolves.toEqual(older)
  })

  it('removes what IndexedDB holds when there is no localStorage copy', async () => {
    const indexedDB = new IDBFactory()
    await createStorage({ indexedDB, localStorage }).saveState(stateOf(exported()))
    const storage = createStorage({ indexedDB, localStorage })

    await storage.discardStoredText()

    await expect(createStorage({ indexedDB, localStorage }).loadState()).resolves.toEqual(
      emptyState(),
    )
  })

  it('removes nothing when IndexedDB cannot be opened', async () => {
    const storage = createStorage({ indexedDB: failingIndexedDB, localStorage })

    await expect(storage.discardStoredText()).rejects.toBeInstanceOf(SavedDataUnreadable)
  })
})

// localStorage that reads and writes but will not remove anything.
function keepingLocalStorage(): Storage {
  return {
    get length() {
      return localStorage.length
    },
    clear: () => localStorage.clear(),
    getItem: (key) => localStorage.getItem(key),
    key: (index) => localStorage.key(index),
    setItem: (key, value) => localStorage.setItem(key, value),
    removeItem: () => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    },
  }
}

describe('a localStorage copy that cannot be removed', () => {
  it('says the stored copy was not removed', async () => {
    localStorage.setItem(STORAGE_KEY, 'not json')
    const storage = createStorage({
      indexedDB: new IDBFactory(),
      localStorage: keepingLocalStorage(),
    })

    await expect(storage.discardStoredText()).rejects.toThrow(
      'this browser did not let the page remove its stored copy',
    )
    expect(localStorage.getItem(STORAGE_KEY)).toBe('not json')
  })

  it('is written over by a save, so the next load still reads the newest state', async () => {
    const indexedDB = new IDBFactory()
    const backends = { indexedDB, localStorage: keepingLocalStorage() }
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...emptyState() }))
    const newer = stateOf(exported())

    await createStorage(backends).saveState(newer)

    await expect(createStorage(backends).loadState()).resolves.toEqual(newer)
  })
})
