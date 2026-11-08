// Where the person's data lives, with the export and the import. Loading a file over a saved
// plan asks first, since it replaces the plan, its week and the meal log.

import { useId, useState, type ChangeEvent } from 'react'

import { saveFile, today } from '../download'
import type { SaveOutcome } from '../session'
import {
  ValidationFailed,
  exportFile,
  loadFile,
  withRules,
  type FieldProblem,
  type State,
} from '../storage'
import { messageOf, sentence } from '../words'

interface Problem {
  message: string
  problems: FieldProblem[]
}

interface Pending {
  state: State
  name: string
  // What the file holds and what loading it does.
  about: string
  // Set when loading removes logged meals, which makes the export first worth offering.
  removes: boolean
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
  blocked,
}: {
  state: State
  onReplace: (state: State) => Promise<SaveOutcome>
  // Why no file can be loaded now: a run would save its week over the loaded plan.
  blocked: string | null
}) {
  const [problem, setProblem] = useState<Problem | null>(null)
  // A loaded file waiting for the person's yes: the state it would make, and what to say about it.
  const [pending, setPending] = useState<Pending | null>(null)
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
      const message = messageOf(error)
      setProblem({ message: `The copy could not be made: ${message}.`, problems: [] })
    }
  }

  function exportRules(): void {
    setProblem(null)
    if (state.rules === null) return
    const name = `meal-planner-rules-${today()}.json`
    saveFile(new Blob([JSON.stringify(state.rules, null, 2)], { type: 'application/json' }), name)
    setStatus(`Exported ${name}.`)
  }

  async function replace(next: State, name: string): Promise<void> {
    setPending(null)
    const outcome = await onReplace(next)
    if (outcome.ok) setStatus(`Loaded ${name}.`)
    // A state that would not load again is refused before anything changes.
    else if (outcome.error instanceof ValidationFailed) {
      setProblem({ message: `${name} was not loaded: ${outcome.error.message}.`, problems: [] })
    } else setStatus(`Loaded ${name} on this page, but it was not saved in this browser.`)
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
    let next: Pending
    try {
      const loaded = await loadFile(file)
      if (loaded.kind === 'export') {
        next = {
          state: loaded.state,
          name: file.name,
          about: `${file.name} has ${described(loaded.state)}. Loading it replaces ${described(state)}. Export a copy first to keep them.`,
          removes: false,
        }
      } else {
        // Rules only: the week was written for other rules, and the log is kept if it fits.
        const { state: made, removed } = withRules(state, loaded.rules)
        const names = (removed?.slotIds ?? []).map(
          (id) => state.rules?.slots.find((slot) => slot.id === id)?.name ?? id,
        )
        let log = ''
        if (removed !== null) {
          const meals = `${removed.entries} logged ${removed.entries === 1 ? 'meal' : 'meals'}`
          log = ` ${meals} for ${names.join(', ')} will be removed, since the plan has no such meal.`
        } else if (state.log.entries.length > 0) {
          log = ' The meal log is kept.'
        }
        next = {
          state: made,
          name: file.name,
          about: `${file.name} has only the rules of the plan ${loaded.rules.name}. The week will be empty, since it was written for other rules.${log} Loading it replaces ${described(state)}. Export a copy first to keep them.`,
          removes: removed !== null,
        }
      }
    } catch (error) {
      if (error instanceof ValidationFailed) {
        setProblem({
          message: `${file.name} does not fit the file format, so nothing was loaded.`,
          problems: error.problems,
        })
      } else {
        const message = messageOf(error)
        setProblem({ message: `${file.name} could not be read: ${message}.`, problems: [] })
      }
      return
    }
    if (isEmpty(state)) await replace(next.state, file.name)
    else setPending(next)
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
        <button
          type="button"
          className="button-secondary"
          disabled={state.rules === null}
          aria-describedby={state.rules === null ? exportHint : undefined}
          onClick={exportRules}
        >
          Export the rules only
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
          A copy exported from this app, or a plan-rules file from a coach, which loads the rules
          only.
        </p>
        {blocked !== null && (
          <p id={`${inputId}-blocked`} className="hint">
            {blocked}
          </p>
        )}
        <input
          id={inputId}
          type="file"
          className="file"
          accept="application/json,.json"
          disabled={blocked !== null}
          aria-describedby={
            blocked === null ? `${inputId}-hint` : `${inputId}-hint ${inputId}-blocked`
          }
          onChange={(event) => void choose(event)}
        />
      </div>
      {pending !== null && (
        <section className="confirm" aria-labelledby="replace-heading">
          <h3 id="replace-heading">Replace what this browser saved?</h3>
          <p>{pending.about}</p>
          <div className="actions">
            <button
              type="button"
              className="button-primary"
              aria-disabled={blocked !== null}
              onClick={() => {
                if (blocked === null) void replace(pending.state, pending.name)
              }}
            >
              Replace with the file
            </button>
            {pending.removes && (
              <button type="button" className="button-secondary" onClick={exportCopy}>
                Export a copy first
              </button>
            )}
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
