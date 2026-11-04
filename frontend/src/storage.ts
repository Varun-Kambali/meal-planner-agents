// The browser keeps the plan rules, the week and the meal log; the API keeps nothing (ADR 0004).
// They are saved in IndexedDB, or in localStorage where IndexedDB cannot be used, and leave the
// browser only as an export file the person saves and can load again.
//
// Every save to IndexedDB removes the localStorage copy, so a copy in localStorage is always the
// newer of the two: it is there only when the last save could not reach IndexedDB.

import { ValidationFailed, checkExport, checkStored, type State } from './validate'

export { ValidationFailed, type FieldProblem, type State } from './validate'

const NOTHING_SAVED: State = { rules: null, week: null, log: { version: 1, entries: [] } }
export const EMPTY_STATE: Readonly<State> = Object.freeze(NOTHING_SAVED)

// The saved record's format, which is the export file's with room for no rules yet.
const STORED_VERSION = 1
const DATABASE = 'meal-planner'
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

export interface Backends {
  indexedDB: IDBFactory | undefined
  localStorage: Storage | undefined
}

export interface StateStore {
  loadState(): Promise<State>
  saveState(state: State): Promise<void>
}

function emptyState(): State {
  return structuredClone(EMPTY_STATE) as State
}

function done<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'))
  })
}

// The database, or null when IndexedDB is missing, refused or fails to open. `open` itself
// throws in some browsers that block site data; the promise turns that into a rejection.
async function openDatabase(factory: IDBFactory | undefined): Promise<IDBDatabase | null> {
  if (factory === undefined) return null
  try {
    return await new Promise<IDBDatabase>((resolve, reject) => {
      const request = factory.open(DATABASE, 1)
      request.onupgradeneeded = () => request.result.createObjectStore(STORE)
      request.onsuccess = () => {
        const database = request.result
        // A newer version of the page in another tab needs this connection closed to upgrade.
        database.onversionchange = () => database.close()
        resolve(database)
      }
      request.onerror = () => reject(request.error ?? new Error('IndexedDB did not open'))
    })
  } catch {
    return null
  }
}

async function readRecord(database: IDBDatabase): Promise<string | undefined> {
  const store = database.transaction(STORE, 'readonly').objectStore(STORE)
  const value: unknown = await done(store.get(RECORD))
  return typeof value === 'string' ? value : undefined
}

// Resolves once the transaction has committed, not only once the put was accepted.
function writeRecord(database: IDBDatabase, text: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE, 'readwrite')
    transaction.oncomplete = () => resolve()
    transaction.onerror = () => reject(transaction.error ?? new Error('IndexedDB write failed'))
    transaction.onabort = () => reject(transaction.error ?? new Error('IndexedDB write aborted'))
    transaction.objectStore(STORE).put(text, RECORD)
  })
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

function removeCopy(storage: Storage | undefined): void {
  try {
    storage?.removeItem(STORAGE_KEY)
  } catch {
    // Nothing was kept there, then.
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
  let database: Promise<IDBDatabase | null> | undefined
  const connection = (): Promise<IDBDatabase | null> =>
    (database ??= openDatabase(backends.indexedDB))

  return {
    // Rejects with ValidationFailed, naming the place, when what was saved no longer fits.
    async loadState(): Promise<State> {
      const copy = readCopy(backends.localStorage)
      if (copy !== null) return fromStored(copy)
      const opened = await connection()
      const text = opened === null ? undefined : await readRecord(opened).catch(() => undefined)
      return text === undefined ? emptyState() : fromStored(text)
    },

    // Rejects with StorageUnavailable when neither backend keeps it.
    async saveState(state: State): Promise<void> {
      const text = JSON.stringify({ version: STORED_VERSION, ...state })
      const opened = await connection()
      if (opened !== null) {
        try {
          await writeRecord(opened, text)
          removeCopy(backends.localStorage)
          return
        } catch {
          // Full or failing: keep it in localStorage instead.
        }
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
