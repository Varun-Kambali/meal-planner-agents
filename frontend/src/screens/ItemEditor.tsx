// One food of a swapped meal as the person changes it: its amount, its unit and, through a
// search, the food itself (SwapFlow.tsx).

import { useId, useRef, useState, type FormEvent } from 'react'

import type { Change, SwapItem } from '../swap'
import { FoodSearch } from './FoodSearch'
import { RequestProblem } from './parts'

// The amount, the unit and the food of one item, as the person changes them. Confirming asks the
// compute route for the numbers; a refusal is shown here with a way to try again.
export function ItemEditor({
  item,
  computing,
  onConfirm,
  onCancel,
}: {
  item: SwapItem
  // Another food's numbers are being worked out, so this one waits.
  computing: boolean
  onConfirm: (change: Change) => Promise<unknown>
  onCancel: (() => void) | null
}) {
  const [food, setFood] = useState(item.food!)
  const [units, setUnits] = useState(item.units)
  const [amount, setAmount] = useState(item.amount === null ? '' : String(item.amount))
  const [unit, setUnit] = useState(
    item.unit !== null && item.units.includes(item.unit) ? item.unit : '',
  )
  const [problem, setProblem] = useState<string | null>(null)
  const [failed, setFailed] = useState<unknown>(null)
  const [working, setWorking] = useState(false)
  const [searching, setSearching] = useState(false)
  const id = useId()
  // Held while another food's numbers are worked out, not this one's.
  const held = computing && !working
  const amountInput = useRef<HTMLInputElement>(null)

  // Never rejects: a refusal is shown under the fields.
  async function confirm(): Promise<void> {
    if (working || computing) return
    const value = Number(amount.trim())
    if (amount.trim() === '' || !Number.isFinite(value) || value <= 0) {
      setProblem('Give the amount as a number above 0.')
      amountInput.current?.focus()
      return
    }
    if (unit === '') {
      setProblem('Choose a unit.')
      return
    }
    setProblem(null)
    setFailed(null)
    setWorking(true)
    const error = await onConfirm({ food, amount: value, unit, units })
    setWorking(false)
    if (error !== null) setFailed(error)
  }

  return (
    <div className="item-editor">
      <form
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          void confirm()
        }}
      >
        <div className="field-grid">
          <div className="field">
            <label htmlFor={`${id}-amount`}>Amount</label>
            <input
              id={`${id}-amount`}
              ref={amountInput}
              className="input"
              inputMode="decimal"
              autoComplete="off"
              value={amount}
              aria-invalid={problem === null ? undefined : true}
              aria-describedby={problem === null ? undefined : `${id}-problem`}
              onChange={(event) => setAmount(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor={`${id}-unit`}>Unit</label>
            <select
              id={`${id}-unit`}
              className="input"
              value={unit}
              onChange={(event) => setUnit(event.target.value)}
            >
              <option value="">Choose a unit</option>
              {units.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        </div>
        {problem !== null && (
          <p id={`${id}-problem`} className="field-error">
            {problem}
          </p>
        )}
        <div className="actions">
          <button
            type="submit"
            className="button-secondary"
            aria-disabled={working || computing}
            aria-describedby={held ? `${id}-held` : undefined}
            aria-label={`Confirm ${food.name}`}
          >
            Confirm
          </button>
          {onCancel !== null && (
            <button type="button" className="button-secondary" onClick={onCancel}>
              Cancel
            </button>
          )}
          <button
            type="button"
            className="button-text"
            aria-expanded={searching}
            onClick={() => setSearching((open) => !open)}
          >
            Change food
          </button>
        </div>
        {held && (
          <p id={`${id}-held`} className="hint">
            Another food&apos;s numbers are being worked out; this one can be confirmed once they
            are in.
          </p>
        )}
        <output>{working ? 'Working out the numbers.' : ''}</output>
      </form>
      {failed !== null && (
        <RequestProblem
          what="The numbers were not worked out."
          error={failed}
          onRetry={() => void confirm()}
        />
      )}
      {searching && (
        <FoodSearch
          initial={item.name}
          onChoose={(option) => {
            setFood({ id: option.id, name: option.name })
            setUnits(option.units)
            setUnit((current) => (option.units.includes(current) ? current : ''))
            setSearching(false)
            amountInput.current?.focus()
          }}
        />
      )}
    </div>
  )
}
