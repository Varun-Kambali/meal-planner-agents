// A swapped meal: the person types what they ate, the meal reader (POST /v1/log/read) reads it
// into nutrition-mcp's foods and amounts, and the person confirms or changes each food before the
// meal is saved. Saving waits while a food needs an amount or a unit. A changed food's numbers
// come from POST /v1/log/compute when it is confirmed, and "Change food" searches nutrition-mcp
// (GET /v1/foods/search) when its form is sent, never as the person types: every /v1/ route
// shares one rate limit. A food the data lacks is listed as missing, left out of the totals and
// not saved; it is never replaced by another food.

import { useEffect, useId, useRef, useState, type FormEvent } from 'react'

import { computeMeal, readMeal, searchFoods } from '../api'
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
import type { FoodOption, LogItem, MealResult } from '../types'
import { NUTRIENTS, measureWords, shown, type NutrientKey } from '../words'
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

function isAbort(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === 'AbortError'
}

// The calls this flow has open, so that closing it stops them.
function useCalls(): () => AbortSignal {
  const open = useRef(new Set<AbortController>())
  useEffect(() => {
    const calls = open.current
    return () => {
      for (const call of calls) call.abort()
    }
  }, [])
  return () => {
    const controller = new AbortController()
    open.current.add(controller)
    return controller.signal
  }
}

export function SwapFlow({ onSave, onCancel }: SwapFlowProps) {
  const [text, setText] = useState('')
  const [textError, setTextError] = useState<string | null>(null)
  const [reading, setReading] = useState<Reading>({ status: 'idle' })
  const [items, setItems] = useState<SwapItem[] | null>(null)
  const [total, setTotal] = useState<MealResult | null>(null)
  // The found foods the person has opened to change.
  const [changing, setChanging] = useState<ReadonlySet<number>>(new Set())
  const [saving, setSaving] = useState(false)
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
      const result = await readMeal(text, { signal: call() })
      showFoods.current = true
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
    try {
      const result = await computeMeal(logItems(sent), { signal: call() })
      setItems(computed(next, sent, result.items, key))
      setTotal(result)
      setChanging((open) => new Set([...open].filter((k) => k !== key)))
      return null
    } catch (error) {
      return isAbort(error) ? null : error
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
  const blocked = items === null || todo.length > 0 || saved.length === 0 || saving

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
                key={item.key}
                item={item}
                changing={changing.has(item.key)}
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
  onConfirm,
  onChange,
  onCancelChange,
  onConfirmChange,
}: {
  item: SwapItem
  changing: boolean
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
          onConfirm={onConfirmChange}
          onCancel={item.status === 'matched' ? onCancelChange : null}
        />
      )}
    </li>
  )
}

// The amount, the unit and the food of one item, as the person changes them. Confirming asks the
// compute route for the numbers; a refusal is shown here with a way to try again.
function ItemEditor({
  item,
  onConfirm,
  onCancel,
}: {
  item: SwapItem
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
  const amountInput = useRef<HTMLInputElement>(null)

  // Never rejects: a refusal is shown under the fields.
  async function confirm(): Promise<void> {
    if (working) return
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
            aria-disabled={working}
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

type Search =
  | { status: 'idle' }
  | { status: 'searching' }
  | { status: 'found'; options: FoodOption[] }
  | { status: 'failed'; error: unknown }

// nutrition-mcp's foods under a name the person types, asked for when the form is sent.
function FoodSearch({
  initial,
  onChoose,
}: {
  initial: string
  onChoose: (option: FoodOption) => void
}) {
  const [query, setQuery] = useState(initial)
  const [search, setSearch] = useState<Search>({ status: 'idle' })
  const [problem, setProblem] = useState<string | null>(null)
  const call = useCalls()
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => input.current?.focus(), [])

  // Never rejects: a search that fails is said to, with a way to search again.
  async function find(): Promise<void> {
    if (query.trim() === '') {
      setProblem('Type a food name first.')
      return
    }
    setProblem(null)
    setSearch({ status: 'searching' })
    try {
      const options = await searchFoods(query.trim(), { signal: call() })
      setSearch({ status: 'found', options })
    } catch (error) {
      if (!isAbort(error)) setSearch({ status: 'failed', error })
    }
  }

  return (
    <div className="food-search">
      <form
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          if (search.status !== 'searching') void find()
        }}
      >
        <label htmlFor={`${id}-q`}>Food name</label>
        <div className="inline">
          <input
            id={`${id}-q`}
            ref={input}
            className="input"
            autoComplete="off"
            maxLength={100}
            value={query}
            aria-invalid={problem === null ? undefined : true}
            aria-describedby={problem === null ? undefined : `${id}-problem`}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            type="submit"
            className="button-secondary"
            aria-disabled={search.status === 'searching'}
          >
            Search
          </button>
        </div>
        {problem !== null && (
          <p id={`${id}-problem`} className="field-error">
            {problem}
          </p>
        )}
        <output>
          {search.status === 'searching' && 'Searching.'}
          {search.status === 'found' &&
            (search.options.length === 0
              ? 'nutrition-mcp has no food under that name.'
              : `${search.options.length} ${search.options.length === 1 ? 'food' : 'foods'} found.`)}
        </output>
      </form>
      {search.status === 'failed' && (
        <RequestProblem
          what="The search did not run."
          error={search.error}
          onRetry={() => void find()}
        />
      )}
      {search.status === 'found' && search.options.length > 0 && (
        <ul className="choices-found" aria-label="Foods found">
          {search.options.map((option) => (
            <li key={option.id}>
              {option.name} <span className="meta">{option.kind}</span>{' '}
              <button
                type="button"
                className="button-text"
                aria-label={`Use ${option.name}`}
                onClick={() => onChoose(option)}
              >
                Use
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
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
