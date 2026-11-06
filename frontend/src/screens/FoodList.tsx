// A list of foods from the catalogue, chosen one at a time, and the line that says once, for every
// such list, that the catalogue could not be loaded.

import { useState } from 'react'

import type { CatalogueState } from '../useCatalogue'
import { errorId, fieldId, focusSoon, type Errors } from './fields'
import { FieldError } from './parts'

interface FoodListProps {
  path: string
  label: string
  hint?: string
  chosen: string[]
  catalogue: CatalogueState
  errors: Errors
  onChange: (chosen: string[]) => void
}

const FOOD_LOAD_ID = 'food-list-load'

// Says once, for both lists of foods, that the catalogue could not be loaded.
export function FoodLoad({
  catalogue,
  onRetry,
}: {
  catalogue: CatalogueState
  onRetry: () => void
}) {
  if (catalogue.status !== 'failed') return null
  return (
    <p id={FOOD_LOAD_ID} className="field-error">
      The food list could not be loaded: {catalogue.message}.{' '}
      <button type="button" className="button-text" onClick={onRetry}>
        Load the food list again
      </button>
    </p>
  )
}

// Foods from the catalogue, chosen one at a time. The ids are what the rules keep.
export function FoodList({
  path,
  label,
  hint,
  chosen,
  catalogue,
  errors,
  onChange,
}: FoodListProps) {
  const [choice, setChoice] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const id = fieldId(path)
  const rows = catalogue.status === 'ready' ? catalogue.rows : []
  const names = new Map(rows.map((row) => [row.id, row.name]))
  const invalid = errors.has(path) || problem !== null
  const describedBy = [
    hint === undefined ? null : `${id}-hint`,
    catalogue.status === 'failed' ? FOOD_LOAD_ID : null,
    errors.has(path) ? errorId(path) : null,
    problem === null ? null : `${id}-problem`,
  ]
    .filter((part) => part !== null)
    .join(' ')
  const add = (): void => {
    if (choice === '') {
      setProblem('Choose a food first.')
      return
    }
    setProblem(null)
    onChange([...chosen, choice])
    setChoice('')
  }
  return (
    <div className="field food-list">
      <label htmlFor={id}>{label}</label>
      {hint !== undefined && (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      )}
      <div className="inline">
        <select
          id={id}
          className="input"
          value={choice}
          disabled={catalogue.status !== 'ready'}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={describedBy || undefined}
          onChange={(event) => setChoice(event.target.value)}
        >
          <option value="">
            {catalogue.status === 'loading' || catalogue.status === 'idle'
              ? 'Loading the food list'
              : 'Choose a food'}
          </option>
          {rows
            .filter((row) => !chosen.includes(row.id))
            .map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
        </select>
        <button
          type="button"
          className="button-secondary"
          disabled={catalogue.status !== 'ready'}
          aria-label={`Add to ${label.toLowerCase()}`}
          onClick={add}
        >
          Add
        </button>
      </div>
      {problem !== null && (
        <p id={`${id}-problem`} className="field-error">
          {problem}
        </p>
      )}
      <FieldError path={path} errors={errors} />
      {chosen.length > 0 && (
        <ul className="chosen" aria-label={`${label}, chosen`}>
          {chosen.map((foodId) => {
            const name = names.get(foodId) ?? foodId
            return (
              <li key={foodId}>
                {name}{' '}
                <button
                  type="button"
                  className="button-text"
                  aria-label={`Remove ${name} from ${label.toLowerCase()}`}
                  onClick={() => {
                    onChange(chosen.filter((item) => item !== foodId))
                    focusSoon(id)
                  }}
                >
                  Remove
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
