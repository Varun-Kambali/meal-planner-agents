// A swapped meal: the person types what they ate, the meal reader (POST /v1/log/read) reads it
// into nutrition-mcp's foods and amounts, and the person confirms or changes each food before the
// meal is saved. Saving waits while a food needs an amount or a unit. A changed food's numbers
// come from POST /v1/log/compute when it is confirmed, and "Change food" searches nutrition-mcp
// (POST /v1/foods/search) when its form is sent, never as the person types: every /v1/ route
// shares one rate limit. A food the data lacks is listed as missing, left out of the totals and
// not saved; it is never replaced by another food.

import { useEffect, useId, useRef, useState, type FormEvent } from 'react'

import { computeMeal, readMeal } from '../api'
import {
  computed,
  counted,
  itemName,
  logItems,
  stillToDo,
  swapItems,
  withChange,
  type Change,
  type SwapItem,
} from '../swap'
import type { LogItem, MealResult } from '../types'
import { isAbort, useCalls } from '../useCalls'
import { NUTRIENTS, measureWords, shown, type NutrientKey } from '../words'
import { ItemEditor } from './ItemEditor'
import { RequestProblem, Status } from './parts'

// The longest typed meal the reader takes (MEAL_PLANNER_MAX_MEAL_TEXT_CHARS).
const MAX_TEXT = 500

// The totals a meal shows while it is confirmed: the six a recipe prints.
const TOTALS: NutrientKey[] = [
  'kcal',
  'protein_g',
  'carbs_available_g',
  'fat_g',
  'sat_fat_g',
  'fibre_g',
]

export interface SwapFlowProps {
  // Saves the meal with these foods; true once it is saved.
  onSave: (items: LogItem[]) => Promise<boolean>
  onCancel: () => void
}

type Reading = { status: 'idle' } | { status: 'reading' } | { status: 'failed'; error: unknown }

export function SwapFlow({ onSave, onCancel }: SwapFlowProps) {
  const [text, setText] = useState('')
  const [textError, setTextError] = useState<string | null>(null)
  const [reading, setReading] = useState<Reading>({ status: 'idle' })
  const [items, setItems] = useState<SwapItem[] | null>(null)
  // Counts the reads that worked, so the foods of a new read start afresh and a compute asked for
  // before it is not applied to them.
  const [reads, setReads] = useState(0)
  const latestRead = useRef(0)
  const [total, setTotal] = useState<MealResult | null>(null)
  // The found foods the person has opened to change.
  const [changing, setChanging] = useState<ReadonlySet<number>>(new Set())
  const [saving, setSaving] = useState(false)
  // One food's numbers are worked out at a time: each compute sends every counted food, so two at
  // once would each undo the other's change.
  const [computing, setComputing] = useState(false)
  const call = useCalls()
  const textId = useId()
  const foodsId = useId()
  const reasonId = useId()
  const textArea = useRef<HTMLTextAreaElement>(null)
  const foodsHeading = useRef<HTMLHeadingElement>(null)
  // Set by a read that worked, so the list it shows takes the focus once, not on every change.
  const showFoods = useRef(false)

  useEffect(() => textArea.current?.focus(), [])
  useEffect(() => {
    if (items !== null && showFoods.current) {
      showFoods.current = false
      foodsHeading.current?.focus()
    }
  }, [items])

  // Never rejects: a read that fails is said to, with a way to read again.
  async function read(): Promise<void> {
    if (text.trim() === '') {
      setTextError('Type what you ate first.')
      textArea.current?.focus()
      return
    }
    setTextError(null)
    setReading({ status: 'reading' })
    try {
      const result = await call((signal) => readMeal(text, { signal }))
      showFoods.current = true
      latestRead.current += 1
      setReads(latestRead.current)
      setItems(swapItems(result))
      setTotal(result.total)
      setChanging(new Set())
      setReading({ status: 'idle' })
    } catch (error) {
      if (!isAbort(error)) setReading({ status: 'failed', error })
    }
  }

  // nutrition-mcp's numbers for the counted foods with the change made, the changed one confirmed.
  // Resolves with null once done, or with the error the compute route gave.
  async function confirmChange(key: number, change: Change): Promise<unknown> {
    if (items === null) return null
    const next = withChange(items, key, change)
    const sent = counted(next)
    const askedUnder = latestRead.current
    setComputing(true)
    try {
      const result = await call((signal) => computeMeal(logItems(sent), { signal }))
      if (askedUnder !== latestRead.current) return null
      // Applied to the foods as they are now, which the person may have confirmed meanwhile.
      setItems((now) => (now === null ? now : computed(now, sent, result.items, key)))
      setTotal(result)
      setChanging((open) => new Set([...open].filter((k) => k !== key)))
      return null
    } catch (error) {
      return isAbort(error) ? null : error
    } finally {
      setComputing(false)
    }
  }

  const todo = items === null ? [] : stillToDo(items, changing)
  const saved = items === null ? [] : counted(items)
  const missing = items?.filter((item) => item.status === 'missing') ?? []
  let reason = ''
  if (todo.length > 0) reason = `Before saving: ${todo.join('; ')}.`
  else if (saved.length === 0) {
    reason =
      'None of the foods read is in the data, so there is nothing to save. Type the meal again in other words, or log it as skipped.'
  } else {
    reason = `Saves ${saved.length} ${saved.length === 1 ? 'food' : 'foods'} as this meal${missing.length > 0 ? `, without ${missing.map(itemName).join(', ')}` : ''}.`
  }
  const blocked = items === null || todo.length > 0 || saved.length === 0 || saving || computing

  async function save(): Promise<void> {
    if (blocked || items === null) return
    setSaving(true)
    const done = await onSave(logItems(items))
    if (!done) setSaving(false)
  }

  const busy = reading.status === 'reading'
  return (
    <div className="panel">
      <form
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          if (!busy) void read()
        }}
      >
        <div className="field">
          <label htmlFor={textId}>What did you eat?</label>
          <p id={`${textId}-hint`} className="hint">
            In your own words, such as 2 rotis and a katori of dal. The foods come from
            nutrition-mcp&apos;s data, and you confirm each one before it is saved.
          </p>
          <textarea
            id={textId}
            ref={textArea}
            className="input textarea"
            rows={3}
            maxLength={MAX_TEXT}
            value={text}
            aria-invalid={textError === null ? undefined : true}
            aria-describedby={
              textError === null ? `${textId}-hint` : `${textId}-hint ${textId}-error`
            }
            onChange={(event) => setText(event.target.value)}
          />
          {textError !== null && (
            <p id={`${textId}-error`} className="field-error">
              {textError}
            </p>
          )}
        </div>
        <div className="actions">
          <button type="submit" className="button-secondary" aria-disabled={busy}>
            {items === null ? 'Read the meal' : 'Read it again'}
          </button>
          {items === null && (
            <button type="button" className="button-secondary" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
        <output>{busy ? 'Reading the meal.' : ''}</output>
      </form>
      {reading.status === 'failed' && (
        <RequestProblem
          what="The meal was not read."
          error={reading.error}
          onRetry={() => void read()}
        />
      )}
      {items !== null && (
        <>
          <h3 id={foodsId} ref={foodsHeading} tabIndex={-1}>
            Foods read
          </h3>
          <ul className="foods" aria-labelledby={foodsId}>
            {items.map((item) => (
              <FoodRow
                key={`${reads}-${item.key}`}
                item={item}
                changing={changing.has(item.key)}
                computing={computing}
                onConfirm={() =>
                  setItems((all) =>
                    all === null
                      ? all
                      : all.map((i) => (i.key === item.key ? { ...i, confirmed: true } : i)),
                  )
                }
                onChange={() => setChanging((open) => new Set([...open, item.key]))}
                onCancelChange={() =>
                  setChanging((open) => new Set([...open].filter((k) => k !== item.key)))
                }
                onConfirmChange={(change) => confirmChange(item.key, change)}
              />
            ))}
          </ul>
          <MealTotals total={total} items={items} />
          <div className="actions">
            <button
              type="button"
              className="button-primary"
              aria-disabled={blocked}
              aria-describedby={reasonId}
              onClick={() => void save()}
            >
              Save the meal
            </button>
            <button type="button" className="button-secondary" onClick={onCancel}>
              Cancel
            </button>
          </div>
          <p id={reasonId} className="hint">
            {reason}
          </p>
        </>
      )}
    </div>
  )
}

// How much was eaten, and nutrition-mcp's grams and calories for it: "3 pieces: 120 g, 357 kcal".
function amountWords(item: SwapItem): string {
  if (item.amount === null || item.unit === null) return ''
  const amount = measureWords({ amount: item.amount, unit: item.unit })
  if (item.numbers === null) return amount
  const kcal = item.numbers.nutrients.kcal
  return `${amount}: ${shown(item.numbers.grams)} g, ${kcal === null ? 'calories not known' : `${shown(kcal)} kcal`}`
}

function FoodRow({
  item,
  changing,
  computing,
  onConfirm,
  onChange,
  onCancelChange,
  onConfirmChange,
}: {
  item: SwapItem
  changing: boolean
  computing: boolean
  onConfirm: () => void
  onChange: () => void
  onCancelChange: () => void
  onConfirmChange: (change: Change) => Promise<unknown>
}) {
  const nameId = useId()
  const name = itemName(item)
  // A found food's amount or unit is missing or unread until the person gives one.
  const editing = item.status === 'needs_amount' || item.status === 'unknown_unit' || changing
  // Confirming a food, or closing its change, removes the button the focus was on; the focus then
  // goes to the food's Change button rather than to the page.
  const changeButton = useRef<HTMLButtonElement>(null)
  const before = useRef({ confirmed: item.confirmed, editing })
  useEffect(() => {
    const moved = before.current.confirmed !== item.confirmed || before.current.editing !== editing
    before.current = { confirmed: item.confirmed, editing }
    if (moved && document.activeElement === document.body) changeButton.current?.focus()
  }, [item.confirmed, editing])
  return (
    <li aria-labelledby={nameId}>
      <p className="food-name">
        <strong id={nameId}>{name}</strong>
        {item.food !== null && item.food.name.toLowerCase() !== item.name.toLowerCase() && (
          <span className="meta"> read from &ldquo;{item.name}&rdquo;</span>
        )}
      </p>
      <p>
        {item.status === 'missing' && (
          <Status verdict="warn" word="Missing:">
            not in the data, so it is left out of the totals and is not saved.
          </Status>
        )}
        {item.status === 'needs_amount' && <Status verdict="warn" word="Needs an amount" />}
        {item.status === 'unknown_unit' && (
          <Status verdict="warn" word="Unit not known:">
            {item.unit ?? 'none'}. nutrition-mcp has no such unit for this food.
          </Status>
        )}
        {item.status === 'matched' && item.confirmed && <Status verdict="ok" word="Confirmed" />}
        {item.status === 'matched' && !item.confirmed && (
          <Status verdict="unsettled" word="Found:">
            confirm it, or change it if it is not what you ate.
          </Status>
        )}
      </p>
      {item.status === 'matched' && <p className="numbers">{amountWords(item)}</p>}
      {item.status === 'matched' && !changing && (
        <div className="actions">
          {!item.confirmed && (
            <button
              type="button"
              className="button-secondary"
              aria-label={`Confirm ${name}`}
              onClick={onConfirm}
            >
              Confirm
            </button>
          )}
          <button
            type="button"
            className="button-text"
            ref={changeButton}
            aria-label={`Change ${name}`}
            onClick={onChange}
          >
            Change
          </button>
        </div>
      )}
      {editing && item.food !== null && (
        <ItemEditor
          item={item}
          computing={computing}
          onConfirm={onConfirmChange}
          onCancel={item.status === 'matched' ? onCancelChange : null}
        />
      )}
    </li>
  )
}

// The server's total for the foods counted so far, and which foods it leaves out and why.
function MealTotals({ total, items }: { total: MealResult | null; items: SwapItem[] }) {
  const missing = items.filter((item) => item.status === 'missing').map(itemName)
  const waiting = items
    .filter((item) => item.status === 'needs_amount' || item.status === 'unknown_unit')
    .map(itemName)
  return (
    <>
      {total === null ? (
        <p>None of the foods read can be counted yet.</p>
      ) : (
        <table className="numbers meal-totals">
          <caption>Total of the foods counted</caption>
          <thead>
            <tr>
              <th scope="col">Nutrient</th>
              <th scope="col" className="num">
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {TOTALS.map((key) => {
              const value = total.total[key]
              const lower = (total.total_incomplete[key] ?? []).length > 0
              return (
                <tr key={key}>
                  <th scope="row">{NUTRIENTS[key].name}</th>
                  <td className="num">
                    {value === null ? (
                      'not known'
                    ) : (
                      <>
                        {lower && 'at least '}
                        {shown(value)} <span className="unit">{NUTRIENTS[key].unit}</span>
                      </>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {missing.length > 0 && (
        <p className="meta">Left out of the totals: {missing.join(', ')}, not in the data.</p>
      )}
      {waiting.length > 0 && (
        <p className="meta">
          Not counted yet: {waiting.join(', ')}, until{' '}
          {waiting.length === 1 ? 'it has' : 'each has'} an amount and a unit nutrition-mcp reads.
        </p>
      )}
    </>
  )
}
