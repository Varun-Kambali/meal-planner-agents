// The browser keeps the plan rules, the week and the meal log; the API keeps nothing (ADR 0004).
// They are saved in IndexedDB, or in localStorage where IndexedDB cannot be used, and leave the
// browser only as an export file the person saves and can load again.
//
// Every save to IndexedDB removes the localStorage copy, so a copy in localStorage is always the
// newer of the two: it is there only when the last save could not reach IndexedDB.

import { ValidationFailed, checkExport, checkStored, type State } from './validate'

export { ValidationFailed, type FieldProblem, type State } from './validate'

// The state before anything is saved, made new each time so that no caller can change another's.
export function emptyState(): State {
  return { rules: null, week: null, log: { version: 1, entries: [] } }
}

// The saved record's format, which is the export file's with room for no rules yet.
const STORED_VERSION = 1
const DATABASE = 'meal-planner'
const DATABASE_VERSION = 1
const STORE = 'state'
const RECORD = 'current'
export const STORAGE_KEY = 'meal-planner-state'

// Neither IndexedDB nor localStorage could keep the state: a browser with site data blocked.
export class StorageUnavailable extends Error {
  constructor() {
    super('this browser does not let the page keep data; export the file to keep a copy')
    this.name = 'StorageUnavailable'
  }
}

// IndexedDB may hold a saved state that cannot be read now: its open request failed, a read
// failed, or a newer version of the page in another tab has upgraded the database. Starting
// empty, or saving a copy elsewhere, could lose that state, so neither is done.
export class SavedDataUnreadable extends Error {
  constructor(cause: unknown) {
    super('what this browser saved cannot be read right now; reload the page to try again', {
      cause,
    })
    this.name = 'SavedDataUnreadable'
  }
}

export interface Backends {
  indexedDB: IDBFactory | undefined
  localStorage: Storage | undefined
}

export interface StateStore {
  loadState(): Promise<State>
  saveState(state: State): Promise<void>
  readStoredText(): Promise<string | null>
  discardStoredText(): Promise<void>
}

function done<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'))
  })
}

// What opening IndexedDB came to. `absent`: the browser has none, or `open` threw at once, as
// it does where site data is blocked, so nothing can have been saved there. `failed`: the open
// request was made and then failed, so a state saved earlier may still be there.
type Opened =
  { kind: 'open'; database: IDBDatabase } | { kind: 'absent' } | { kind: 'failed'; error: unknown }

// `closed` is called when the browser closes the connection, or another tab asks for it to be
// closed so that it can upgrade the database, so that the next call opens it again.
function openDatabase(factory: IDBFactory | undefined, closed: () => void): Promise<Opened> {
  if (factory === undefined) return Promise.resolve({ kind: 'absent' })
  let request: IDBOpenDBRequest
  try {
    request = factory.open(DATABASE, DATABASE_VERSION)
  } catch {
    return Promise.resolve({ kind: 'absent' })
  }
  return new Promise((resolve) => {
    request.onupgradeneeded = (event) => {
      // Each later version adds its own step here, from the version the browser has.
      if (event.oldVersion < 1) request.result.createObjectStore(STORE)
    }
    request.onsuccess = () => {
      const database = request.result
      database.onversionchange = () => {
        database.close()
        closed()
      }
      database.onclose = closed
      resolve({ kind: 'open', database })
    }
    request.onerror = () => {
      resolve({ kind: 'failed', error: request.error ?? new Error('IndexedDB did not open') })
    }
  })
}

async function readRecord(database: IDBDatabase): Promise<string | undefined> {
  const store = database.transaction(STORE, 'readonly').objectStore(STORE)
  const value: unknown = await done(store.get(RECORD))
  return typeof value === 'string' ? value : undefined
}

// Resolves once the transaction has committed, not only once the change was accepted.
function changeRecord(
  database: IDBDatabase,
  change: (store: IDBObjectStore) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE, 'readwrite')
    transaction.oncomplete = () => resolve()
    transaction.onerror = () => reject(transaction.error ?? new Error('IndexedDB write failed'))
    transaction.onabort = () => reject(transaction.error ?? new Error('IndexedDB write aborted'))
    change(transaction.objectStore(STORE))
  })
}

function writeRecord(database: IDBDatabase, text: string): Promise<void> {
  return changeRecord(database, (store) => store.put(text, RECORD))
}

// localStorage throws when it is blocked or full; each of these says so by its result instead.
function readCopy(storage: Storage | undefined): string | null {
  try {
    return storage?.getItem(STORAGE_KEY) ?? null
  } catch {
    return null
  }
}

function writeCopy(storage: Storage | undefined, text: string): boolean {
  if (storage === undefined) return false
  try {
    storage.setItem(STORAGE_KEY, text)
    return true
  } catch {
    return false
  }
}

// Whether the copy is gone: false when localStorage holds one and refused to remove it.
function removeCopy(storage: Storage | undefined): boolean {
  try {
    storage?.removeItem(STORAGE_KEY)
    return true
  } catch {
    return readCopy(storage) === null
  }
}

function parsed(text: string): unknown {
  try {
    return JSON.parse(text) as unknown
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    throw new ValidationFailed([{ path: '', message: `is not JSON (${reason})` }])
  }
}

function fromStored(text: string): State {
  const { version: _version, ...state } = checkStored(parsed(text))
  return state
}

// Storage over the given backends. The page uses the browser's (`loadState` and `saveState`
// below); tests pass their own.
export function createStorage(backends: Backends): StateStore {
  let database: Promise<Opened> | undefined
  const forget = (): void => {
    database = undefined
  }
  // A connection that failed to open is not kept, so the next call tries again.
  const connection = async (): Promise<Opened> => {
    database ??= openDatabase(backends.indexedDB, forget)
    const opened = await database
    if (opened.kind === 'failed') forget()
    return opened
  }

  // The text loadState reads: the localStorage copy when there is one, since it is the newer,
  // and otherwise what IndexedDB holds. Null when nothing was saved.
  const storedText = async (): Promise<string | null> => {
    const copy = readCopy(backends.localStorage)
    if (copy !== null) return copy
    const opened = await connection()
    if (opened.kind === 'absent') return null
    if (opened.kind === 'failed') throw new SavedDataUnreadable(opened.error)
    try {
      return (await readRecord(opened.database)) ?? null
    } catch (error) {
      forget()
      throw new SavedDataUnreadable(error)
    }
  }

  return {
    // Rejects with ValidationFailed, naming the place, when what was saved no longer fits, and
    // with SavedDataUnreadable when IndexedDB may hold a state it cannot read now. Starting
    // empty in either case would let the next save write over the plan.
    async loadState(): Promise<State> {
      const text = await storedText()
      return text === null ? emptyState() : fromStored(text)
    },

    // The stored text as it is, checked or not, so that the person can keep a copy of a state
    // the page refuses to open. Rejects with SavedDataUnreadable as loadState does.
    readStoredText: storedText,

    // Removes the text loadState reads, and nothing else. When that was the localStorage copy,
    // the next load opens the older state IndexedDB may hold. Rejects with SavedDataUnreadable,
    // removing nothing, when IndexedDB cannot be opened.
    async discardStoredText(): Promise<void> {
      if (readCopy(backends.localStorage) !== null) {
        if (!removeCopy(backends.localStorage)) {
          throw new Error('this browser did not let the page remove its stored copy')
        }
        return
      }
      const opened = await connection()
      if (opened.kind === 'failed') throw new SavedDataUnreadable(opened.error)
      if (opened.kind === 'absent') return
      try {
        await changeRecord(opened.database, (store) => store.delete(RECORD))
      } catch (error) {
        forget()
        throw new SavedDataUnreadable(error)
      }
    },

    // The state is checked first, as loadState will check it: one that would not load again is
    // refused with ValidationFailed and nothing is written, so the last good state stays. Rejects
    // with SavedDataUnreadable rather than write a localStorage copy over a state IndexedDB may
    // hold, and with StorageUnavailable when neither backend keeps it.
    async saveState(state: State): Promise<void> {
      const text = JSON.stringify({ version: STORED_VERSION, ...state })
      checkStored(JSON.parse(text))
      const opened = await connection()
      if (opened.kind === 'open') {
        let written = false
        try {
          await writeRecord(opened.database, text)
          written = true
        } catch {
          // Full or failing: keep it in localStorage instead. The copy is the newer one now.
        }
        // A copy left in localStorage would be read first next time, so one that cannot be
        // removed is made the newer state too.
        if (written && removeCopy(backends.localStorage)) return
      } else if (opened.kind === 'failed' && readCopy(backends.localStorage) === null) {
        throw new SavedDataUnreadable(opened.error)
      }
      if (!writeCopy(backends.localStorage, text)) throw new StorageUnavailable()
    },
  }
}

// Reading either global can throw in a browser that blocks site data.
function browserBackends(): Backends {
  const get = <T>(read: () => T): T | undefined => {
    try {
      return read() ?? undefined
    } catch {
      return undefined
    }
  }
  return {
    indexedDB: get(() => globalThis.indexedDB),
    localStorage: get(() => globalThis.localStorage),
  }
}

let browserStorage: StateStore | undefined

function browser(): StateStore {
  return (browserStorage ??= createStorage(browserBackends()))
}

export function loadState(): Promise<State> {
  return browser().loadState()
}

export function saveState(state: State): Promise<void> {
  return browser().saveState(state)
}

export function readStoredText(): Promise<string | null> {
  return browser().readStoredText()
}

export function discardStoredText(): Promise<void> {
  return browser().discardStoredText()
}

// The export file: the state as one JSON file in the format of schemas/export.json.
export function exportFile(state: State): Blob {
  if (state.rules === null) {
    throw new Error('there is nothing to export until the plan rules are saved')
  }
  const file = { version: 1, rules: state.rules, week: state.week, log: state.log }
  return new Blob([JSON.stringify(file, null, 2)], { type: 'application/json' })
}

// A file the person chose, checked against schemas/export.json and the rules the schema cannot
// say. Rejects with ValidationFailed, whose problems name each failing place by its path.
export async function importFile(file: Blob): Promise<State> {
  const { rules, week, log } = checkExport(parsed(await file.text()))
  return { rules, week, log }
}
