// The plan form: the rules of the meal plan the person already follows. It checks what it can
// before saving (planForm.ts), and shows the problems the API found when a week was written,
// each under its field.

import { useEffect, useId, useRef, useState, type FormEvent, type RefObject } from 'react'

import {
  FIELD_LABELS,
  SLOT_LABELS,
  TARGET_FIELDS,
  buildRules,
  draftOf,
  fromStored,
  labelOf,
  sameRules,
  withoutSlots,
  type FormError,
  type PlanDraft,
  type Removal,
} from '../planForm'
import { saveFile, today } from '../download'
import type { Save } from '../session'
import { ValidationFailed, exportFile, type State } from '../storage'
import type { PlanRules } from '../types'
import type { SharedCatalogue } from '../useCatalogue'
import { ALLERGENS, DAYS, DAY_NAMES, DIETS, GEAR, messageOf } from '../words'
import { DayTypes } from './DayTypes'
import { byField, errorId, fieldId, type SectionProps } from './fields'
import { FoodList, FoodLoad } from './FoodList'
import { Choices, FieldError, ProblemList, SelectField, TextField } from './parts'
import { Slots } from './Slots'

export interface PlanFormProps {
  state: State
  // The API's problems with the saved rules, from the last time a week was asked for.
  refused: FormError[]
  onSave: Save
  // Why saving has to wait, such as a week being written for the saved plan.
  blocked: string | null
  // The catalogue the shell holds, for the lists of foods (useCatalogue).
  catalogue: SharedCatalogue
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

type Saving =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'saved' }
  | { status: 'failed'; message: string }

export function PlanForm({ state, refused, onSave, blocked, catalogue }: PlanFormProps) {
  const [draft, setDraft] = useState<PlanDraft>(() => draftOf(state.rules))
  const [errors, setErrors] = useState<FormError[]>([])
  const [saving, setSaving] = useState<Saving>({ status: 'idle' })
  const [pending, setPending] = useState<PlanRules | null>(null)
  // Logged meals the plan drops, offered for removal; and why the copy of the file could not be made.
  const [removal, setRemoval] = useState<Removal | null>(null)
  const [exportProblem, setExportProblem] = useState<string | null>(null)
  const summary = useRef<HTMLElement>(null)
  const question = useRef<HTMLHeadingElement>(null)
  const focusSummary = useRef(false)
  const blockedId = useId()

  // The whole catalogue, asked for by the shell once per page and again on asking: the list is
  // not asked for again as the diet or the allergens change.
  const { need: needCatalogue } = catalogue
  useEffect(() => needCatalogue(), [needCatalogue])

  useEffect(() => {
    if (errors.length > 0 && focusSummary.current) {
      focusSummary.current = false
      summary.current?.focus()
    }
  }, [errors])

  useEffect(() => {
    if (pending !== null || removal !== null) question.current?.focus()
  }, [pending, removal])

  const paths = fieldPaths(draft)
  const place = (path: string): string => nearest(path, paths)
  const shownErrors = byField(
    [...refused, ...errors].map((error) => ({ ...error, path: place(error.path) })),
  )
  const change = (update: (draft: PlanDraft) => PlanDraft): void => {
    setDraft(update)
    setRemoval(null)
    setSaving((current) => (current.status === 'saved' ? { status: 'idle' } : current))
  }

  async function commit(
    rules: PlanRules,
    clearWeek: boolean,
    removeSlots: readonly string[] = [],
  ): Promise<void> {
    setPending(null)
    setRemoval(null)
    setSaving({ status: 'saving' })
    const outcome = await onSave((current) => ({
      ...current,
      rules,
      week: clearWeek ? null : current.week,
      log: removeSlots.length === 0 ? current.log : withoutSlots(current.log, removeSlots),
    }))
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
    const message = messageOf(outcome.error)
    setSaving({ status: 'failed', message })
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const built = buildRules(draft, state.log)
    setRemoval(null)
    setExportProblem(null)
    if (built.rules === null) {
      setSaving({ status: 'idle' })
      if (built.removal !== undefined) {
        // The offer says it all, so the list of problems is not shown as well.
        setErrors([])
        setRemoval(built.removal)
        return
      }
      focusSummary.current = true
      setErrors(built.errors)
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
          <FoodLoad catalogue={catalogue.state} onRetry={catalogue.reload} />
          <FoodList
            path="food.exclude_foods"
            label="Foods to leave out"
            chosen={draft.excludeFoods}
            catalogue={catalogue.state}
            errors={shownErrors}
            onChange={(excludeFoods) => change((d) => ({ ...d, excludeFoods }))}
          />
          <FoodList
            path="food.liked_foods"
            label="Foods you like"
            hint="A liked food the diet or the allergens rule out is not used."
            chosen={draft.likedFoods}
            catalogue={catalogue.state}
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
      {removal !== null && (
        <RemovalOffer
          removal={removal}
          state={state}
          headingRef={question}
          problem={exportProblem}
          onExportProblem={setExportProblem}
          onRemove={() => void commit(removal.rules, state.week !== null, removal.slotIds)}
          onCancel={() => setRemoval(null)}
        />
      )}
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

// "Lunch", "Lunch and Snack", "Breakfast, Lunch and Snack".
function listed(names: string[]): string {
  if (names.length < 2) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

// The plan drops a meal the log has entries for. Nothing is removed until the person says so, and
// the file they can export first keeps the entries.
function RemovalOffer({
  removal,
  state,
  headingRef,
  problem,
  onExportProblem,
  onRemove,
  onCancel,
}: {
  removal: Removal
  state: State
  headingRef: RefObject<HTMLHeadingElement | null>
  problem: string | null
  onExportProblem: (message: string | null) => void
  onRemove: () => void
  onCancel: () => void
}) {
  const names = removal.slotIds.map(
    (id) => state.rules?.slots.find((slot) => slot.id === id)?.name ?? id,
  )
  const meals = `${removal.entries} logged ${removal.entries === 1 ? 'meal' : 'meals'}`
  return (
    <section className="notice" aria-labelledby="drop-meals-heading">
      <h2 id="drop-meals-heading" ref={headingRef} tabIndex={-1}>
        This plan drops meals you have logged
      </h2>
      <p>
        Your meal log has {meals} for {listed(names)}, which this plan no longer has. Saving the
        plan removes {removal.entries === 1 ? 'it' : 'them'} from the log
        {state.week !== null &&
          ', and clears the written week, which was checked against the plan as it was'}
        . Export a copy first: the file keeps them.
      </p>
      <div className="actions">
        <button type="button" className="button-primary" onClick={onRemove}>
          Save and remove the {meals} for {listed(names)}
        </button>
        <button
          type="button"
          className="button-secondary"
          onClick={() => {
            try {
              saveFile(exportFile(state), `meal-planner-${today()}.json`)
              onExportProblem(null)
            } catch (error) {
              onExportProblem(messageOf(error))
            }
          }}
        >
          Export a copy first
        </button>
        <button type="button" className="button-secondary" onClick={onCancel}>
          Keep editing
        </button>
      </div>
      {problem !== null && <p>The copy could not be made: {problem}.</p>}
    </section>
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
