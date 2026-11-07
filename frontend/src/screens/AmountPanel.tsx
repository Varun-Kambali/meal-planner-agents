// "Different amount" on the log screen (Today.tsx): how much of the planned recipe was eaten.

import { useEffect, useId, useRef, useState, type FormEvent } from 'react'

import { MAX_MEAL_GRAMS, recipeGrams, recipeItems } from '../mealLog'
import type { CheckedRecipe, LogItem } from '../types'
import { shown } from '../words'

type Portion = 'half' | 'more' | 'grams'

const PORTIONS: [Portion, string][] = [
  ['half', 'Half'],
  ['more', 'One and a half'],
  ['grams', 'A weight in grams'],
]

// How much of the recipe was eaten: every ingredient is scaled by the same factor, half, one and
// a half, or the grams of the whole meal eaten over the recipe's own weight.
export function AmountPanel({
  recipe,
  onSave,
  onCancel,
}: {
  recipe: CheckedRecipe
  onSave: (items: LogItem[]) => Promise<boolean>
  onCancel: () => void
}) {
  const [portion, setPortion] = useState<Portion | null>(null)
  const [grams, setGrams] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const name = useId()
  const id = useId()
  const first = useRef<HTMLInputElement>(null)
  const gramsInput = useRef<HTMLInputElement>(null)
  useEffect(() => first.current?.focus(), [])
  const weight = recipeGrams(recipe)

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    if (saving) return
    let factor: number
    if (portion === null) {
      setProblem('Choose how much of the recipe you ate.')
      first.current?.focus()
      return
    }
    if (portion === 'grams') {
      const value = Number(grams.trim())
      if (grams.trim() === '' || !Number.isFinite(value) || value <= 0 || value > MAX_MEAL_GRAMS) {
        setProblem(`Give the grams as a number above 0 and at most ${MAX_MEAL_GRAMS}.`)
        gramsInput.current?.focus()
        return
      }
      factor = value / weight
    } else factor = portion === 'half' ? 0.5 : 1.5
    setProblem(null)
    setSaving(true)
    const done = await onSave(recipeItems(recipe, factor))
    if (!done) setSaving(false)
  }

  return (
    <form className="panel" noValidate onSubmit={(event) => void submit(event)}>
      <fieldset className="choices">
        <legend>How much of the recipe did you eat?</legend>
        <div className="choice-list">
          {PORTIONS.map(([value, label], index) => (
            <label key={value} className="choice">
              <input
                type="radio"
                name={name}
                value={value}
                ref={index === 0 ? first : undefined}
                checked={portion === value}
                aria-describedby={
                  problem !== null && portion === null ? `${id}-problem` : undefined
                }
                onChange={() => setPortion(value)}
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      {portion === 'grams' && (
        <div className="field">
          <label htmlFor={`${id}-grams`}>Grams of the whole meal</label>
          <p id={`${id}-hint`} className="hint">
            The recipe as written weighs {shown(weight)} g. Every ingredient is scaled to match.
          </p>
          <input
            id={`${id}-grams`}
            ref={gramsInput}
            className="input"
            inputMode="decimal"
            autoComplete="off"
            value={grams}
            aria-invalid={problem === null ? undefined : true}
            aria-describedby={problem === null ? `${id}-hint` : `${id}-hint ${id}-problem`}
            onChange={(event) => setGrams(event.target.value)}
          />
        </div>
      )}
      {problem !== null && (
        <p id={`${id}-problem`} className="field-error">
          {problem}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="button-primary" aria-disabled={saving}>
          Save the amount
        </button>
        <button type="button" className="button-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}
