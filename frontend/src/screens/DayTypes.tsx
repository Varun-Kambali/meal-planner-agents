// The plan form's day types: each kind of day with its daily targets, and the extra targets of
// each.

import {
  MAX_DAY_TYPES,
  TARGET_FIELDS,
  TARGET_LABELS,
  emptyDayType,
  emptyExtra,
  type DayTypeDraft,
  type PlanDraft,
} from '../planForm'
import { DAYS, EXTRA_NUTRIENTS, NUTRIENTS } from '../words'
import { fieldId, focusSoon, type Errors, type SectionProps } from './fields'
import { FieldError, SelectField, TextField } from './parts'

export function DayTypes({ draft, change, errors }: SectionProps) {
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
