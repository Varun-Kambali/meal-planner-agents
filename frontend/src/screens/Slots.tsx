// The plan form's meals of the day, each with its share of the day's calories and protein.

import { MAX_SLOTS, SLOT_LABELS, emptySlot, type SlotDraft } from '../planForm'
import { COOK_WHEN } from '../words'
import { fieldId, focusSoon, type SectionProps } from './fields'
import { FieldError, SelectField, TextField } from './parts'

// The total of the shares as typed, for the line under the meals. A share that is not a number
// yet leaves the total out.
function shareTotal(slots: SlotDraft[], field: 'kcal_share' | 'protein_share'): string | null {
  const values = slots.map((slot) => Number(slot[field].trim() || Number.NaN))
  if (values.some(Number.isNaN)) return null
  return String(Math.round(values.reduce((sum, value) => sum + value, 0) * 10) / 10)
}

export function Slots({ draft, change, errors }: SectionProps) {
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
