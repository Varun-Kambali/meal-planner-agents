// The plan form: the rules of the meal plan the person already follows. It checks what it can
// before saving (planForm.ts), and shows the problems the API found when a week was written,
// each under its field.

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'

import { catalogue as loadCatalogue } from '../api'
import {
  FIELD_LABELS,
  MAX_DAY_TYPES,
  SLOT_LABELS,
  TARGET_LABELS,
  labelOf,
  MAX_SLOTS,
  TARGET_FIELDS,
  buildRules,
  draftOf,
  emptyDayType,
  emptyExtra,
  emptySlot,
  fromStored,
  sameRules,
  type DayTypeDraft,
  type FormError,
  type PlanDraft,
  type SlotDraft,
} from '../planForm'
import type { SaveOutcome } from '../session'
import { ValidationFailed, type State } from '../storage'
import type { CatalogueRow, PlanRules } from '../types'
import {
  ALLERGENS,
  COOK_WHEN,
  DAYS,
  DAY_NAMES,
  DIETS,
  EXTRA_NUTRIENTS,
  GEAR,
  NUTRIENTS,
} from '../words'
import { byField, errorId, fieldId, focusSoon, type Errors } from './fields'
import { Choices, FieldError, ProblemList, SelectField, TextField } from './parts'

export interface PlanFormProps {
  state: State
  // The API's problems with the saved rules, from the last time a week was asked for.
  refused: FormError[]
  onSave: (state: State) => Promise<SaveOutcome>
  // Why saving has to wait, such as a week being written for the saved plan.
  blocked: string | null
}

// The fields this draft has, by path, so that a problem can be put at the nearest one.
function fieldPaths(draft: PlanDraft): Set<string> {
  const paths = new Set(Object.keys(FIELD_LABELS))
  for (const day of DAYS) paths.add(`week.${day}`)
  draft.dayTypes.forEach((dayType, i) => {
    paths.add(`day_types.${i}`)
    paths.add(`day_types.${i}.name`)
    for (const field of TARGET_FIELDS) paths.add(`day_types.${i}.${field}`)
    paths.add(`day_types.${i}.extra`)
    dayType.extra.forEach((_target, j) => {
      for (const field of ['nutrient', 'kind', 'value']) {
        paths.add(`day_types.${i}.extra.${j}.${field}`)
      }
    })
  })
  draft.slots.forEach((_slot, i) => {
    paths.add(`slots.${i}`)
    for (const field of Object.keys(SLOT_LABELS)) paths.add(`slots.${i}.${field}`)
  })
  return paths
}

function nearest(path: string, paths: Set<string>): string {
  let parts = path.split('.')
  while (parts.length > 0) {
    const candidate = parts.join('.')
    if (paths.has(candidate)) return candidate
    parts = parts.slice(0, -1)
  }
  return ''
}

type Food =
  | { status: 'loading' }
  | { status: 'ready'; rows: CatalogueRow[] }
  | {
      status: 'failed'
      message: string
    }

type Saving =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'saved' }
  | { status: 'failed'; message: string }

export function PlanForm({ state, refused, onSave, blocked }: PlanFormProps) {
  const [draft, setDraft] = useState<PlanDraft>(() => draftOf(state.rules))
  const [errors, setErrors] = useState<FormError[]>([])
  const [saving, setSaving] = useState<Saving>({ status: 'idle' })
  const [pending, setPending] = useState<PlanRules | null>(null)
  const [food, setFood] = useState<Food>({ status: 'loading' })
  const summary = useRef<HTMLElement>(null)
  const question = useRef<HTMLHeadingElement>(null)
  const focusSummary = useRef(false)
  const blockedId = useId()

  // The catalogue, once per visit to the form and again on asking: every /v1/ route shares one
  // rate limit, so the list is not asked for again as the diet or the allergens change.
  const foodRequest = useRef<AbortController | null>(null)
  const loadFood = useCallback((): void => {
    foodRequest.current?.abort()
    const controller = new AbortController()
    foodRequest.current = controller
    loadCatalogue({}, { signal: controller.signal }).then(
      (rows) => {
        if (!controller.signal.aborted) setFood({ status: 'ready', rows })
      },
      (error: unknown) => {
        if (controller.signal.aborted) return
        const message = error instanceof Error ? error.message : String(error)
        setFood({ status: 'failed', message })
      },
    )
  }, [])
  useEffect(() => {
    loadFood()
    return () => foodRequest.current?.abort()
  }, [loadFood])

  useEffect(() => {
    if (errors.length > 0 && focusSummary.current) {
      focusSummary.current = false
      summary.current?.focus()
    }
  }, [errors])

  useEffect(() => {
    if (pending !== null) question.current?.focus()
  }, [pending])

  const paths = fieldPaths(draft)
  const place = (path: string): string => nearest(path, paths)
  const shownErrors = byField(
    [...refused, ...errors].map((error) => ({ ...error, path: place(error.path) })),
  )
  const change = (update: (draft: PlanDraft) => PlanDraft): void => {
    setDraft(update)
    setSaving((current) => (current.status === 'saved' ? { status: 'idle' } : current))
  }

  async function commit(rules: PlanRules, clearWeek: boolean): Promise<void> {
    setPending(null)
    setSaving({ status: 'saving' })
    const outcome = await onSave({ ...state, rules, week: clearWeek ? null : state.week })
    if (outcome.ok) {
      setErrors([])
      setSaving({ status: 'saved' })
      // New day types and meals now have ids; keep them, so the next save names the same ones.
      setDraft((current) => ({
        ...current,
        dayTypes: current.dayTypes.map((row, i) => ({
          ...row,
          id: rules.day_types[i]?.id ?? row.id,
        })),
        slots: current.slots.map((row, i) => ({ ...row, id: rules.slots[i]?.id ?? row.id })),
      }))
      return
    }
    if (outcome.error instanceof ValidationFailed) {
      focusSummary.current = true
      setErrors(fromStored(outcome.error.problems))
      setSaving({ status: 'idle' })
      return
    }
    const message = outcome.error instanceof Error ? outcome.error.message : String(outcome.error)
    setSaving({ status: 'failed', message })
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const built = buildRules(draft, state.log)
    if (built.rules === null) {
      focusSummary.current = true
      setErrors(built.errors)
      setSaving({ status: 'idle' })
      return
    }
    setErrors([])
    const changed = !sameRules(built.rules, state.rules)
    if (changed && state.week !== null) {
      setPending(built.rules)
      return
    }
    await commit(built.rules, false)
  }

  const count = errors.length
  return (
    <>
      <h1 tabIndex={-1} data-screen-title>
        Your plan
      </h1>
      <p>
        The rules of the meal plan you follow. Each week of recipes is written for them and checked
        against them.
      </p>
      <ProblemList
        heading="The API refused the plan"
        intro="It said so when the week was asked for. Fix these, save the plan, then write the week again."
        errors={refused}
        label={labelOf}
        place={place}
      />
      <ProblemList
        heading={
          count === 1
            ? 'There is a problem with the plan'
            : `There are ${count} problems with the plan`
        }
        errors={errors}
        label={labelOf}
        place={place}
        summaryRef={summary}
      />
      <form id={fieldId('')} tabIndex={-1} noValidate onSubmit={(event) => void submit(event)}>
        <TextField
          path="name"
          label="Plan name"
          value={draft.name}
          maxLength={60}
          errors={shownErrors}
          onChange={(name) => change((d) => ({ ...d, name }))}
        />
        <DayTypes draft={draft} change={change} errors={shownErrors} />
        <WeekDays draft={draft} change={change} errors={shownErrors} />
        <Slots draft={draft} change={change} errors={shownErrors} />
        <Choices
          path="gear"
          legend="Kitchen gear you own"
          kind="checkbox"
          options={Object.entries(GEAR) as [keyof typeof GEAR, string][]}
          chosen={draft.gear}
          errors={shownErrors}
          onChange={(gear) => change((d) => ({ ...d, gear }))}
        />
        <fieldset className="section">
          <legend>Food</legend>
          <Choices
            path="food.diet"
            legend="Diet"
            kind="radio"
            options={Object.entries(DIETS) as [keyof typeof DIETS, string][]}
            chosen={draft.diet === '' ? [] : [draft.diet]}
            errors={shownErrors}
            onChange={([diet]) => change((d) => ({ ...d, diet: diet ?? '' }))}
          />
          <Choices
            path="food.exclude_allergens"
            legend="Allergens to leave out"
            kind="checkbox"
            options={Object.entries(ALLERGENS) as [keyof typeof ALLERGENS, string][]}
            chosen={draft.allergens}
            errors={shownErrors}
            onChange={(allergens) => change((d) => ({ ...d, allergens }))}
          />
          <FoodLoad
            food={food}
            onRetry={() => {
              setFood({ status: 'loading' })
              loadFood()
            }}
          />
          <FoodList
            path="food.exclude_foods"
            label="Foods to leave out"
            chosen={draft.excludeFoods}
            food={food}
            errors={shownErrors}
            onChange={(excludeFoods) => change((d) => ({ ...d, excludeFoods }))}
          />
          <FoodList
            path="food.liked_foods"
            label="Foods you like"
            hint="A liked food the diet or the allergens rule out is not used."
            chosen={draft.likedFoods}
            food={food}
            errors={shownErrors}
            onChange={(likedFoods) => change((d) => ({ ...d, likedFoods }))}
          />
          <div className="field">
            <label htmlFor={fieldId('food.cuisines')}>Cuisines to draw on</label>
            <p id={`${fieldId('food.cuisines')}-hint`} className="hint">
              One per line, such as South Indian or Japanese. Up to 10.
            </p>
            <textarea
              id={fieldId('food.cuisines')}
              className="input textarea"
              rows={4}
              value={draft.cuisines}
              aria-invalid={shownErrors.has('food.cuisines') ? true : undefined}
              aria-describedby={[
                `${fieldId('food.cuisines')}-hint`,
                shownErrors.has('food.cuisines') ? errorId('food.cuisines') : '',
              ]
                .join(' ')
                .trim()}
              onChange={(event) => change((d) => ({ ...d, cuisines: event.target.value }))}
            />
            <FieldError path="food.cuisines" errors={shownErrors} />
          </div>
        </fieldset>
        <Choices
          path="shopping_days"
          legend="Shopping days"
          kind="checkbox"
          hint="The days you buy food. A food that keeps only a few days once opened is checked against them."
          options={DAYS.map((day) => [day, DAY_NAMES[day]])}
          chosen={draft.shoppingDays}
          errors={shownErrors}
          onChange={(shoppingDays) => change((d) => ({ ...d, shoppingDays }))}
        />
        <div className="actions">
          <button
            type="submit"
            className="button-primary"
            disabled={blocked !== null || saving.status === 'saving'}
            aria-describedby={blocked === null ? undefined : blockedId}
          >
            Save the plan
          </button>
          {blocked !== null && (
            <p id={blockedId} className="hint">
              {blocked}
            </p>
          )}
        </div>
        <SaveStatus saving={saving} />
      </form>
      {pending !== null && (
        <section className="notice" aria-labelledby="clear-week-heading">
          <h2 id="clear-week-heading" ref={question} tabIndex={-1}>
            This plan has a written week
          </h2>
          <p>
            Its recipes were checked against the plan as it was. Saving the changes clears that
            week, and the next one is written for the changed plan. Export a copy first to keep the
            old week.
          </p>
          <div className="actions">
            <button
              type="button"
              className="button-primary"
              onClick={() => void commit(pending, true)}
            >
              Save and clear the week
            </button>
            <button type="button" className="button-secondary" onClick={() => setPending(null)}>
              Keep editing
            </button>
          </div>
        </section>
      )}
    </>
  )
}

function SaveStatus({ saving }: { saving: Saving }) {
  let text = ''
  if (saving.status === 'saving') text = 'Saving.'
  if (saving.status === 'saved') text = 'Plan saved in this browser.'
  if (saving.status === 'failed') {
    text = `Not saved in this browser: ${saving.message}. The plan is kept on this page until it is closed.`
  }
  return (
    <output className={saving.status === 'failed' ? 'save-status failed' : 'save-status'}>
      {text}
    </output>
  )
}

interface SectionProps {
  draft: PlanDraft
  change: (update: (draft: PlanDraft) => PlanDraft) => void
  errors: Errors
}

function DayTypes({ draft, change, errors }: SectionProps) {
  const update = (key: string, edit: (row: DayTypeDraft) => DayTypeDraft): void =>
    change((d) => ({
      ...d,
      dayTypes: d.dayTypes.map((row) => (row.key === key ? edit(row) : row)),
    }))
  const remove = (key: string): void =>
    change((d) => {
      const dayTypes = d.dayTypes.filter((row) => row.key !== key)
      const first = dayTypes[0]!.key
      const week = Object.fromEntries(
        DAYS.map((day) => [day, d.week[day] === key ? first : d.week[day]]),
      ) as PlanDraft['week']
      return { ...d, dayTypes, week }
    })
  return (
    <fieldset id={fieldId('day_types')} tabIndex={-1} className="section">
      <legend>Day types</legend>
      <p className="hint">
        Each kind of day you eat differently on, such as a training day and a rest day, with its
        daily targets.
      </p>
      <FieldError path="day_types" errors={errors} />
      {draft.dayTypes.map((row, i) => {
        const at = `day_types.${i}`
        return (
          <fieldset key={row.key} id={fieldId(at)} tabIndex={-1} className="row">
            <legend>Day type {i + 1}</legend>
            <FieldError path={at} errors={errors} />
            <TextField
              path={`${at}.name`}
              label="Name"
              value={row.name}
              maxLength={60}
              errors={errors}
              onChange={(name) => update(row.key, (r) => ({ ...r, name }))}
            />
            <div className="field-grid">
              {TARGET_FIELDS.map((field) => (
                <TextField
                  key={field}
                  path={`${at}.${field}`}
                  label={TARGET_LABELS[field]}
                  inputMode="decimal"
                  value={row[field]}
                  errors={errors}
                  onChange={(value) => update(row.key, (r) => ({ ...r, [field]: value }))}
                />
              ))}
            </div>
            <ExtraTargets at={at} row={row} errors={errors} update={update} />
            {draft.dayTypes.length > 1 && (
              <button
                type="button"
                className="button-text"
                onClick={() => {
                  remove(row.key)
                  focusSoon(fieldId('day_types'))
                }}
              >
                Remove day type {i + 1}
              </button>
            )}
          </fieldset>
        )
      })}
      {draft.dayTypes.length < MAX_DAY_TYPES && (
        <button
          type="button"
          className="button-secondary"
          onClick={() => change((d) => ({ ...d, dayTypes: [...d.dayTypes, emptyDayType()] }))}
        >
          Add a day type
        </button>
      )}
    </fieldset>
  )
}

function ExtraTargets({
  at,
  row,
  errors,
  update,
}: {
  at: string
  row: DayTypeDraft
  errors: Errors
  update: (key: string, edit: (row: DayTypeDraft) => DayTypeDraft) => void
}) {
  const path = `${at}.extra`
  const editTarget = (key: string, edit: Partial<DayTypeDraft['extra'][number]>): void =>
    update(row.key, (r) => ({
      ...r,
      extra: r.extra.map((target) => (target.key === key ? { ...target, ...edit } : target)),
    }))
  return (
    <fieldset id={fieldId(path)} tabIndex={-1} className="subsection">
      <legend>Other daily targets</legend>
      <FieldError path={path} errors={errors} />
      {row.extra.length === 0 && <p className="hint">None. Add one for iron or calcium, say.</p>}
      {row.extra.map((target, j) => (
        <div key={target.key} className="field-grid target">
          <SelectField
            path={`${path}.${j}.nutrient`}
            label="Nutrient"
            value={target.nutrient}
            errors={errors}
            options={[
              { value: '', label: 'Choose a nutrient' },
              ...EXTRA_NUTRIENTS.map((key) => ({
                value: key,
                label: `${NUTRIENTS[key].name} (${NUTRIENTS[key].unit})`,
              })),
            ]}
            onChange={(nutrient) =>
              editTarget(target.key, { nutrient: nutrient as typeof target.nutrient })
            }
          />
          <SelectField
            path={`${path}.${j}.kind`}
            label="At least or at most"
            value={target.kind}
            errors={errors}
            options={[
              { value: 'min', label: 'At least' },
              { value: 'max', label: 'At most' },
            ]}
            onChange={(kind) => editTarget(target.key, { kind: kind as typeof target.kind })}
          />
          <TextField
            path={`${path}.${j}.value`}
            label="Amount"
            inputMode="decimal"
            value={target.value}
            errors={errors}
            onChange={(value) => editTarget(target.key, { value })}
          />
          <button
            type="button"
            className="button-text"
            onClick={() => {
              update(row.key, (r) => ({ ...r, extra: r.extra.filter((t) => t.key !== target.key) }))
              focusSoon(fieldId(path))
            }}
          >
            Remove target {j + 1}
          </button>
        </div>
      ))}
      {row.extra.length < EXTRA_NUTRIENTS.length && (
        <button
          type="button"
          className="button-secondary"
          onClick={() => update(row.key, (r) => ({ ...r, extra: [...r.extra, emptyExtra()] }))}
        >
          Add a target
        </button>
      )}
    </fieldset>
  )
}

function WeekDays({ draft, change, errors }: SectionProps) {
  const options = draft.dayTypes.map((row, i) => ({
    value: row.key,
    label: row.name.trim() === '' ? `Day type ${i + 1}` : row.name,
  }))
  return (
    <fieldset id={fieldId('week')} tabIndex={-1} className="section">
      <legend>The week</legend>
      <p className="hint">Which day type each day of the week is.</p>
      <FieldError path="week" errors={errors} />
      <div className="field-grid days">
        {DAYS.map((day) => (
          <SelectField
            key={day}
            path={`week.${day}`}
            label={DAY_NAMES[day]}
            value={draft.week[day]}
            options={options}
            errors={errors}
            onChange={(value) => change((d) => ({ ...d, week: { ...d.week, [day]: value } }))}
          />
        ))}
      </div>
    </fieldset>
  )
}

// The total of the shares as typed, for the line under the meals. A share that is not a number
// yet leaves the total out.
function shareTotal(slots: SlotDraft[], field: 'kcal_share' | 'protein_share'): string | null {
  const values = slots.map((slot) => Number(slot[field].trim() || Number.NaN))
  if (values.some(Number.isNaN)) return null
  return String(Math.round(values.reduce((sum, value) => sum + value, 0) * 10) / 10)
}

function Slots({ draft, change, errors }: SectionProps) {
  const update = (key: string, edit: Partial<SlotDraft>): void =>
    change((d) => ({
      ...d,
      slots: d.slots.map((row) => (row.key === key ? { ...row, ...edit } : row)),
    }))
  const calories = shareTotal(draft.slots, 'kcal_share')
  const protein = shareTotal(draft.slots, 'protein_share')
  return (
    <fieldset id={fieldId('slots')} tabIndex={-1} className="section">
      <legend>Meals of the day</legend>
      <p className="hint">
        Each meal you eat, with its share of the day. The shares of all meals add up to 100%.
      </p>
      <FieldError path="slots" errors={errors} />
      {draft.slots.map((row, i) => {
        const at = `slots.${i}`
        return (
          <fieldset key={row.key} id={fieldId(at)} tabIndex={-1} className="row">
            <legend>Meal {i + 1}</legend>
            <FieldError path={at} errors={errors} />
            <div className="field-grid">
              <TextField
                path={`${at}.name`}
                label={SLOT_LABELS.name}
                value={row.name}
                maxLength={60}
                errors={errors}
                onChange={(name) => update(row.key, { name })}
              />
              <TextField
                path={`${at}.eat_at`}
                label={SLOT_LABELS.eat_at}
                type="time"
                value={row.eat_at}
                errors={errors}
                onChange={(eat_at) => update(row.key, { eat_at })}
              />
              <TextField
                path={`${at}.kcal_share`}
                label={SLOT_LABELS.kcal_share}
                inputMode="decimal"
                value={row.kcal_share}
                errors={errors}
                onChange={(kcal_share) => update(row.key, { kcal_share })}
              />
              <TextField
                path={`${at}.protein_share`}
                label={SLOT_LABELS.protein_share}
                inputMode="decimal"
                value={row.protein_share}
                errors={errors}
                onChange={(protein_share) => update(row.key, { protein_share })}
              />
            </div>
            <TextField
              path={`${at}.style`}
              label={SLOT_LABELS.style}
              hint="Such as overnight oats, a packed lunch box or an air-fryer dish."
              value={row.style}
              maxLength={80}
              errors={errors}
              onChange={(style) => update(row.key, { style })}
            />
            <div className="field-grid">
              <SelectField
                path={`${at}.cook_when`}
                label={SLOT_LABELS.cook_when}
                value={row.cook_when}
                errors={errors}
                options={Object.entries(COOK_WHEN).map(([value, label]) => ({ value, label }))}
                onChange={(cook_when) =>
                  update(row.key, { cook_when: cook_when as SlotDraft['cook_when'] })
                }
              />
              <TextField
                path={`${at}.max_active_min`}
                label={SLOT_LABELS.max_active_min}
                inputMode="numeric"
                value={row.max_active_min}
                errors={errors}
                onChange={(max_active_min) => update(row.key, { max_active_min })}
              />
              <TextField
                path={`${at}.max_total_min`}
                label={SLOT_LABELS.max_total_min}
                inputMode="numeric"
                value={row.max_total_min}
                errors={errors}
                onChange={(max_total_min) => update(row.key, { max_total_min })}
              />
            </div>
            {draft.slots.length > 1 && (
              <button
                type="button"
                className="button-text"
                onClick={() => {
                  change((d) => ({ ...d, slots: d.slots.filter((slot) => slot.key !== row.key) }))
                  focusSoon(fieldId('slots'))
                }}
              >
                Remove meal {i + 1}
              </button>
            )}
          </fieldset>
        )
      })}
      <p className="numbers">
        Calorie shares: {calories === null ? 'not all filled in' : `${calories}% of the day`}.
        Protein shares: {protein === null ? 'not all filled in' : `${protein}% of the day`}.
      </p>
      {draft.slots.length < MAX_SLOTS && (
        <button
          type="button"
          className="button-secondary"
          onClick={() => change((d) => ({ ...d, slots: [...d.slots, emptySlot()] }))}
        >
          Add a meal
        </button>
      )}
    </fieldset>
  )
}

interface FoodListProps {
  path: string
  label: string
  hint?: string
  chosen: string[]
  food: Food
  errors: Errors
  onChange: (chosen: string[]) => void
}

const FOOD_LOAD_ID = 'food-list-load'

// Says once, for both lists of foods, that the catalogue could not be loaded.
function FoodLoad({ food, onRetry }: { food: Food; onRetry: () => void }) {
  if (food.status !== 'failed') return null
  return (
    <p id={FOOD_LOAD_ID} className="field-error">
      The food list could not be loaded: {food.message}.{' '}
      <button type="button" className="button-text" onClick={onRetry}>
        Load the food list again
      </button>
    </p>
  )
}

// Foods from the catalogue, chosen one at a time. The ids are what the rules keep.
function FoodList({ path, label, hint, chosen, food, errors, onChange }: FoodListProps) {
  const [choice, setChoice] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const id = fieldId(path)
  const rows = food.status === 'ready' ? food.rows : []
  const names = new Map(rows.map((row) => [row.id, row.name]))
  const invalid = errors.has(path) || problem !== null
  const describedBy = [
    hint === undefined ? null : `${id}-hint`,
    food.status === 'failed' ? FOOD_LOAD_ID : null,
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
          disabled={food.status !== 'ready'}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={describedBy || undefined}
          onChange={(event) => setChoice(event.target.value)}
        >
          <option value="">
            {food.status === 'loading' ? 'Loading the food list' : 'Choose a food'}
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
          disabled={food.status !== 'ready'}
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
