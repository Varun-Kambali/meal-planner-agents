// Where the person's data lives, with the export and the import. Loading a file over a saved
// plan asks first, since it replaces the plan, its week and the meal log.

import { useId, useState, type ChangeEvent } from 'react'

import { saveFile, today } from '../download'
import type { SaveOutcome } from '../session'
import { ValidationFailed, exportFile, importFile, type FieldProblem, type State } from '../storage'
import { sentence } from '../words'

interface Problem {
  message: string
  problems: FieldProblem[]
}

function described(state: State): string {
  const recipes = state.week?.recipes.length ?? 0
  const meals = state.log.entries.length
  const name = state.rules === null ? 'no plan' : `the plan ${state.rules.name}`
  return `${name}, with ${recipes} ${recipes === 1 ? 'recipe' : 'recipes'} and ${meals} logged ${meals === 1 ? 'meal' : 'meals'}`
}

function isEmpty(state: State): boolean {
  return state.rules === null && state.week === null && state.log.entries.length === 0
}

export function DataSection({
  state,
  onReplace,
}: {
  state: State
  onReplace: (state: State) => Promise<SaveOutcome>
}) {
  const [problem, setProblem] = useState<Problem | null>(null)
  const [pending, setPending] = useState<{ state: State; name: string } | null>(null)
  const [status, setStatus] = useState('')
  const inputId = useId()
  const exportHint = useId()

  function exportCopy(): void {
    setProblem(null)
    try {
      const name = `meal-planner-${today()}.json`
      saveFile(exportFile(state), name)
      setStatus(`Exported ${name}.`)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setProblem({ message: `The copy could not be made: ${message}.`, problems: [] })
    }
  }

  async function replace(next: State, name: string): Promise<void> {
    setPending(null)
    const outcome = await onReplace(next)
    if (outcome.ok) setStatus(`Loaded ${name}.`)
    else setStatus(`Loaded ${name} on this page, but it was not saved in this browser.`)
  }

  // Never rejects: a file that cannot be read or does not fit is said to.
  async function choose(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const input = event.target
    const file = input.files?.[0]
    // Cleared so that choosing the same file again, once fixed, loads it again.
    input.value = ''
    if (file === undefined) return
    setProblem(null)
    setStatus('')
    let next: State
    try {
      next = await importFile(file)
    } catch (error) {
      if (error instanceof ValidationFailed) {
        setProblem({
          message: `${file.name} does not fit the file format, so nothing was loaded.`,
          problems: error.problems,
        })
      } else {
        const message = error instanceof Error ? error.message : String(error)
        setProblem({ message: `${file.name} could not be read: ${message}.`, problems: [] })
      }
      return
    }
    if (isEmpty(state)) await replace(next, file.name)
    else setPending({ state: next, name: file.name })
  }

  return (
    <section className="notice data" aria-labelledby="data-heading">
      <h2 id="data-heading">Your data</h2>
      <p>
        Your plan, its week of recipes and your meal log are saved in this browser only. Clearing
        this browser&apos;s data deletes them, so export a copy to keep one.
      </p>
      <div className="actions">
        <button
          type="button"
          className="button-secondary"
          disabled={state.rules === null}
          aria-describedby={state.rules === null ? exportHint : undefined}
          onClick={exportCopy}
        >
          Export a copy
        </button>
        {state.rules === null && (
          <p id={exportHint} className="hint">
            There is nothing to export until a plan is saved.
          </p>
        )}
      </div>
      <div className="field">
        <label htmlFor={inputId}>Load a file</label>
        <p id={`${inputId}-hint`} className="hint">
          A copy exported from this app, or a plan file from a coach.
        </p>
        <input
          id={inputId}
          type="file"
          className="file"
          accept="application/json,.json"
          aria-describedby={`${inputId}-hint`}
          onChange={(event) => void choose(event)}
        />
      </div>
      {pending !== null && (
        <section className="confirm" aria-labelledby="replace-heading">
          <h3 id="replace-heading">Replace what this browser saved?</h3>
          <p>
            {pending.name} has {described(pending.state)}. Loading it replaces {described(state)}.
            Export a copy first to keep them.
          </p>
          <div className="actions">
            <button
              type="button"
              className="button-primary"
              onClick={() => void replace(pending.state, pending.name)}
            >
              Replace with the file
            </button>
            <button type="button" className="button-secondary" onClick={() => setPending(null)}>
              Cancel
            </button>
          </div>
        </section>
      )}
      {problem !== null && (
        <div role="alert" className="problem">
          <p>{problem.message}</p>
          {problem.problems.length > 0 && (
            <ul>
              {problem.problems.map((item, index) => (
                <li key={index}>
                  <code>{item.path || '/'}</code> {sentence(item.message)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <output className="save-status">{status}</output>
    </section>
  )
}
