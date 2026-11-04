// What the page shows when it cannot open what this browser saved. It says what happened, offers
// the stored copy as a file, and only once that is saved offers to remove the copy so that the
// page can open. Nothing is written over the stored copy before the person asks.

import { useState } from 'react'

import { saveFile, today } from '../download'
import { SavedDataUnreadable, ValidationFailed, type StateStore } from '../storage'
import { sentence } from '../words'

export interface StorageErrorProps {
  error: unknown
  store: StateStore
  // Reads what the browser saved again.
  onReload: () => void
}

type Copy =
  | { status: 'idle' }
  | { status: 'saved'; name: string }
  | { status: 'none' }
  | { status: 'unreadable'; message: string }

const SHOWN_PROBLEMS = 10

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function What({ error }: { error: unknown }) {
  if (error instanceof ValidationFailed) {
    const more = error.problems.length - SHOWN_PROBLEMS
    return (
      <>
        <p>
          What this browser saved does not fit the file format, so the page has not opened it and
          has not changed it. These are the places that do not fit:
        </p>
        <ul>
          {error.problems.slice(0, SHOWN_PROBLEMS).map((problem, index) => (
            <li key={index}>
              <code>{problem.path || '/'}</code> {problem.message}
            </li>
          ))}
        </ul>
        {more > 0 && <p>And {more} more.</p>}
      </>
    )
  }
  if (error instanceof SavedDataUnreadable) {
    return (
      <>
        <p>{sentence(error.message)}.</p>
        <p>
          This can happen while the page is open in another tab that is being updated, or when the
          browser has lost its connection to its storage. Close any other tab with this page, then
          try again.
        </p>
      </>
    )
  }
  return <p>What this browser saved could not be read: {messageOf(error)}.</p>
}

export function StorageError({ error, store, onReload }: StorageErrorProps) {
  const [copy, setCopy] = useState<Copy>({ status: 'idle' })
  const [problem, setProblem] = useState<string | null>(null)

  // Never rejects: a copy that cannot be read is said to.
  async function saveCopy(): Promise<void> {
    setProblem(null)
    let text: string | null
    try {
      text = await store.readStoredText()
    } catch (reason) {
      setCopy({ status: 'unreadable', message: messageOf(reason) })
      return
    }
    if (text === null) {
      setCopy({ status: 'none' })
      return
    }
    const name = `meal-planner-stored-copy-${today()}.json`
    try {
      saveFile(new Blob([text], { type: 'application/json' }), name)
    } catch (reason) {
      setCopy({ status: 'unreadable', message: `the file could not be made: ${messageOf(reason)}` })
      return
    }
    setCopy({ status: 'saved', name })
  }

  // Never rejects: a removal that fails is said to, and the screen stays.
  async function remove(): Promise<void> {
    setProblem(null)
    try {
      await store.discardStoredText()
    } catch (reason) {
      setProblem(`The stored copy was not removed: ${messageOf(reason)}.`)
      return
    }
    onReload()
  }

  return (
    <>
      <h1 tabIndex={-1} data-screen-title>
        Your saved plan could not be opened
      </h1>
      <What error={error} />
      <section aria-labelledby="keep-copy-heading" className="group">
        <h2 id="keep-copy-heading">Keep a copy first</h2>
        <p>
          Save what this browser holds as a file. It keeps your plan, week and meal log as they were
          saved, even where they do not fit, so they can be mended by hand or sent with a bug
          report.
        </p>
        <div className="actions">
          <button type="button" className="button-primary" onClick={() => void saveCopy()}>
            Save the stored copy as a file
          </button>
          <button type="button" className="button-secondary" onClick={onReload}>
            Try again
          </button>
        </div>
        <output>
          {copy.status === 'saved' && `Saved as ${copy.name}.`}
          {copy.status === 'none' &&
            'This browser holds no stored copy now. Try again to open the page.'}
        </output>
        {copy.status === 'unreadable' && (
          <p role="alert">
            The stored copy cannot be read either: {copy.message}. Nothing has been changed; try
            again later.
          </p>
        )}
      </section>
      {copy.status === 'saved' && (
        <section aria-labelledby="start-again-heading" className="group">
          <h2 id="start-again-heading">Start again</h2>
          <p>
            The file you saved keeps the stored copy. Removing it from this browser lets the page
            open: it then opens any older copy this browser has, or an empty plan.
          </p>
          <button type="button" className="button-secondary" onClick={() => void remove()}>
            Remove the stored copy
          </button>
          {problem !== null && <p role="alert">{problem}</p>}
        </section>
      )}
    </>
  )
}
