// Small pieces the screens share: form fields with their errors, the list of problems at the top
// of a form, and status words.

import { useId, type ReactNode, type RefObject } from 'react'

import type { FormError } from '../planForm'
import { errorId, fieldId, goToField, type Errors } from './fields'

export function FieldError({ path, errors }: { path: string; errors: Errors }) {
  const messages = errors.get(path)
  if (messages === undefined) return null
  return (
    <p id={errorId(path)} className="field-error">
      {messages.join(' ')}
    </p>
  )
}

interface TextFieldProps {
  path: string
  label: string
  value: string
  onChange: (value: string) => void
  errors: Errors
  hint?: string
  type?: 'text' | 'time'
  inputMode?: 'decimal' | 'numeric' | 'text'
  maxLength?: number
  className?: string
}

export function TextField(props: TextFieldProps) {
  const { path, label, value, onChange, errors, hint, type = 'text', inputMode } = props
  const id = fieldId(path)
  const invalid = errors.has(path)
  const describedBy =
    [hint === undefined ? null : `${id}-hint`, invalid ? errorId(path) : null]
      .filter((part) => part !== null)
      .join(' ') || undefined
  return (
    <div className={`field ${props.className ?? ''}`}>
      <label htmlFor={id}>{label}</label>
      {hint !== undefined && (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      )}
      <input
        id={id}
        className="input"
        type={type}
        inputMode={inputMode}
        autoComplete="off"
        maxLength={props.maxLength}
        value={value}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      />
      <FieldError path={path} errors={errors} />
    </div>
  )
}

interface SelectFieldProps {
  path: string
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (value: string) => void
  errors: Errors
  className?: string
}

export function SelectField(props: SelectFieldProps) {
  const { path, label, value, options, onChange, errors } = props
  const id = fieldId(path)
  const invalid = errors.has(path)
  return (
    <div className={`field ${props.className ?? ''}`}>
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="input"
        value={value}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={invalid ? errorId(path) : undefined}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <FieldError path={path} errors={errors} />
    </div>
  )
}

interface ChoicesProps<T extends string> {
  path: string
  legend: string
  kind: 'checkbox' | 'radio'
  options: [T, string][]
  chosen: readonly T[]
  onChange: (chosen: T[]) => void
  errors: Errors
  hint?: string
}

// A group of checkboxes or radio buttons, with its problems under the legend.
export function Choices<T extends string>(props: ChoicesProps<T>) {
  const { path, legend, kind, options, chosen, onChange, errors, hint } = props
  const name = useId()
  const invalid = errors.has(path)
  const hintId = `${fieldId(path)}-hint`
  const describedBy =
    [hint === undefined ? null : hintId, invalid ? errorId(path) : null]
      .filter((part) => part !== null)
      .join(' ') || undefined
  const toggle = (value: T, on: boolean): void => {
    if (kind === 'radio') onChange([value])
    else onChange(on ? [...chosen, value] : chosen.filter((item) => item !== value))
  }
  return (
    <fieldset id={fieldId(path)} tabIndex={-1} className="choices" aria-describedby={describedBy}>
      <legend>{legend}</legend>
      {hint !== undefined && (
        <p id={hintId} className="hint">
          {hint}
        </p>
      )}
      <FieldError path={path} errors={errors} />
      <div className="choice-list">
        {options.map(([value, label]) => (
          <label key={value} className="choice">
            <input
              type={kind}
              name={name}
              value={value}
              checked={chosen.includes(value)}
              onChange={(event) => toggle(value, event.target.checked)}
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

interface ProblemListProps {
  heading: string
  intro?: string
  errors: FormError[]
  label: (path: string) => string
  summaryRef?: RefObject<HTMLElement | null>
  // The fields that exist; a problem for any other place links to the form itself.
  place: (path: string) => string
}

// The problems of a form, listed at its top, each linking to its field.
export function ProblemList(props: ProblemListProps) {
  const { heading, intro, errors, label, summaryRef, place } = props
  const headingId = useId()
  if (errors.length === 0) return null
  return (
    <section ref={summaryRef} className="problems" tabIndex={-1} aria-labelledby={headingId}>
      <h2 id={headingId}>{heading}</h2>
      {intro !== undefined && <p>{intro}</p>}
      <ul>
        {errors.map((error, index) => {
          const at = place(error.path)
          const id = fieldId(at)
          const where = label(at)
          return (
            <li key={`${error.path}-${index}`}>
              <a
                href={`#${id}`}
                onClick={(event) => {
                  event.preventDefault()
                  goToField(id)
                }}
              >
                {where === '' ? error.message : `${where}: ${error.message}`}
              </a>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export type Verdict = 'ok' | 'fail' | 'warn' | 'unsettled'

const VERDICT_WORDS: Record<Verdict, string> = {
  ok: 'Meets',
  fail: 'Misses',
  warn: 'Warning',
  unsettled: "Can't tell",
}

// A status is a word, then the reason in body text. Colour repeats the word, never replaces it.
export function Status({
  verdict,
  word,
  children,
}: {
  verdict: Verdict
  word?: string
  children?: ReactNode
}) {
  return (
    <>
      <span className={`status status-${verdict}`}>{word ?? VERDICT_WORDS[verdict]}</span>
      {children !== undefined && <> {children}</>}
    </>
  )
}
