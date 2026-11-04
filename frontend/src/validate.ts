// Checks a file a person loads, and what this browser saved before, in three steps: against
// schemas/export.json; then the rules a JSON Schema cannot say, which the server's models check
// in code (src/meal_planner/rules.py, recipes.py and summary.py); then one rule the models do
// not make, that each log entry's slot is one the plan has. That last is the summary's check
// (summary.log_problems): the model would accept such a file, but every summary of it would be
// refused, so it is refused here, when the file is loaded. Each problem names its place as a
// JSON Pointer, such as /log/entries/0/items/0/food_id, so a person can find it.
// tests/unit/test_export_schemas.py runs the server over src/test/invalid-exports.json, the files
// these checks refuse, to keep the two in step.

import type { ErrorObject, ValidateFunction } from 'ajv/dist/2020'

import { exported, stored } from './generated/validators.js'
import type { Export, MealLog, PlanRules, WeekResult } from './types'

export interface FieldProblem {
  // A JSON Pointer into the data; '' is the whole of it.
  path: string
  message: string
}

export class ValidationFailed extends Error {
  readonly problems: FieldProblem[]

  constructor(problems: FieldProblem[]) {
    const [first] = problems
    const where = first === undefined ? '' : `${first.path || 'the file'} ${first.message}`
    const more = problems.length > 1 ? ` (and ${problems.length - 1} more)` : ''
    super(`the data does not fit the file format: ${where}${more}`)
    this.name = 'ValidationFailed'
    this.problems = problems
  }
}

// What the browser keeps: an export file's parts, with no plan rules until they are saved.
export interface State {
  rules: PlanRules | null
  week: WeekResult | null
  log: MealLog
}

// The days a log may cover, and the slack slot shares get to add up to a day, as on the server.
const LOG_DAYS = 7
const SHARE_SUM_TOLERANCE = 0.001
// The nutrients a day type has a field for, and the field (rules.CORE_TARGET_FIELDS).
const CORE_TARGET_FIELDS: Record<string, string> = {
  kcal: 'kcal',
  protein_g: 'protein_g',
  sat_fat_g: 'sat_fat_g_max',
  fibre_g: 'fibre_g_min',
  carbs_available_g: 'carbs_g',
  fat_g: 'fat_g',
}

// The problems a validator finds in some data; none when it fits. The validators were compiled
// from schemas/export.json ahead of time (scripts/generate-validators.ts), so nothing is compiled
// in the browser. Each fills in a field the server has a default for, so the data matches its
// type.
function errorsOf(validate: ValidateFunction, data: unknown): ErrorObject[] {
  return validate(data) ? [] : (validate.errors ?? [])
}

// An export file as it was saved, checked. Throws ValidationFailed with every problem found.
export function checkExport(data: unknown): Export {
  throwIfAny(schemaProblems(errorsOf(exported, data)))
  const file = data as Export
  throwIfAny([
    ...blankProblems(file.rules, file.week),
    ...ruleProblems(file.rules),
    ...logProblems(file.log, file.rules),
  ])
  return file
}

// What this browser saved, checked the same way; it may have no plan rules yet.
export function checkStored(data: unknown): State & { version: 1 } {
  throwIfAny(schemaProblems(errorsOf(stored, data)))
  const state = data as State & { version: 1 }
  const rules = state.rules === null ? [] : ruleProblems(state.rules)
  throwIfAny([
    ...blankProblems(state.rules, state.week),
    ...rules,
    ...logProblems(state.log, state.rules),
  ])
  return state
}

function throwIfAny(problems: FieldProblem[]): void {
  if (problems.length > 0) throw new ValidationFailed(problems)
}

function pointer(path: string, key: string | number): string {
  return `${path}/${String(key).replaceAll('~', '~0').replaceAll('/', '~1')}`
}

function typeName(type: unknown): string {
  const names: Record<string, string> = {
    array: 'a list',
    boolean: 'true or false',
    integer: 'a whole number',
    null: 'null',
    number: 'a number',
    object: 'an object',
    string: 'a string',
  }
  return names[String(type)] ?? String(type)
}

// Ajv's errors in words a person can act on, each at the field it is about.
function described(error: ErrorObject): FieldProblem {
  const at = error.instancePath
  const p = error.params as Record<string, unknown>
  switch (error.keyword) {
    case 'required':
      return { path: pointer(at, String(p.missingProperty)), message: 'is missing' }
    case 'additionalProperties':
      return {
        path: pointer(at, String(p.additionalProperty)),
        message: 'is not a field of the file',
      }
    case 'propertyNames':
      return { path: pointer(at, String(p.propertyName)), message: 'is not an allowed name here' }
    case 'type':
      return { path: at, message: `must be ${typeName(p.type)}` }
    case 'const':
      return { path: at, message: `must be ${JSON.stringify(p.allowedValue)}` }
    case 'enum':
      return {
        path: at,
        message: `must be one of: ${(p.allowedValues as unknown[]).map(String).join(', ')}`,
      }
    case 'minLength':
      return {
        path: at,
        message:
          p.limit === 1 ? 'must not be empty' : `must have at least ${String(p.limit)} characters`,
      }
    case 'maxLength':
      return { path: at, message: `must have at most ${String(p.limit)} characters` }
    case 'minItems':
      return { path: at, message: `must list at least ${String(p.limit)}` }
    case 'maxItems':
      return { path: at, message: `must list at most ${String(p.limit)}` }
    case 'minProperties':
      return { path: at, message: `must name at least ${String(p.limit)}` }
    case 'minimum':
      return { path: at, message: `must be at least ${String(p.limit)}` }
    case 'maximum':
      return { path: at, message: `must be at most ${String(p.limit)}` }
    case 'exclusiveMinimum':
      return { path: at, message: `must be more than ${String(p.limit)}` }
    case 'exclusiveMaximum':
      return { path: at, message: `must be less than ${String(p.limit)}` }
    case 'format':
      return {
        path: at,
        message: p.format === 'date' ? 'must be a date (YYYY-MM-DD)' : 'must be a time (HH:MM)',
      }
    default:
      return { path: at, message: `does not meet the schema's ${error.keyword} rule` }
  }
}

// Pydantic writes a value that may be null as "anyOf: [the type, null]", so a wrong value fails
// both: Ajv reports what the type wants, that it is not null, and that no branch matched. Only
// the first says what to fix. The other two are dropped wherever a problem of the value itself,
// or of something inside it, is also reported.
function isNullBranch(error: ErrorObject): boolean {
  return (
    error.keyword === 'type' &&
    (error.params as { type?: unknown }).type === 'null' &&
    error.schemaPath.includes('/anyOf/')
  )
}

function schemaProblems(errors: ErrorObject[]): FieldProblem[] {
  const real = errors.filter(
    (error) => error.keyword !== 'anyOf' && error.keyword !== 'oneOf' && !isNullBranch(error),
  )
  const covered = (error: ErrorObject): boolean =>
    real.some(
      (other) =>
        other.instancePath === error.instancePath ||
        other.instancePath.startsWith(`${error.instancePath}/`),
    )
  const kept = errors.filter((error) => real.includes(error) || !covered(error))
  const seen = new Set<string>()
  return kept.map(described).filter((problem) => {
    const key = `${problem.path} ${problem.message}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

// The server strips the whitespace from these strings before it checks their length (Name and
// Label in rules.py, Title, Cuisine and Step in recipes.py), which JSON Schema cannot say: "   "
// fits the schema's minLength of 1 and is still blank to the server.
function blankProblems(rules: PlanRules | null, week: WeekResult | null): FieldProblem[] {
  const strings: [string, string][] = []
  if (rules !== null) {
    strings.push(['/rules/name', rules.name])
    rules.day_types.forEach((dayType, i) =>
      strings.push([`/rules/day_types/${i}/name`, dayType.name]),
    )
    rules.slots.forEach((slot, i) => strings.push([`/rules/slots/${i}/name`, slot.name]))
    rules.food.cuisines.forEach((cuisine, i) =>
      strings.push([`/rules/food/cuisines/${i}`, cuisine]),
    )
  }
  week?.recipes.forEach(({ draft }, i) => {
    const at = `/week/recipes/${i}/draft`
    strings.push([`${at}/title`, draft.title], [`${at}/cuisine`, draft.cuisine])
    draft.steps.forEach((step, j) => strings.push([`${at}/steps/${j}`, step]))
  })
  return strings
    .filter(([, text]) => text.trim() === '')
    .map(([path]) => ({ path, message: 'must not be blank' }))
}

// PlanRules' validators: unique ids, a defined day type for each day, extra targets that are
// new, and slot shares that add up to a day.
function ruleProblems(rules: PlanRules, at = '/rules'): FieldProblem[] {
  const problems: FieldProblem[] = []
  const repeated = (ids: string[], field: string): void => {
    ids.forEach((id, index) => {
      if (ids.indexOf(id) !== index) {
        problems.push({
          path: pointer(pointer(pointer(at, field), index), 'id'),
          message: `'${id}' is used twice; ${field} ids must be unique`,
        })
      }
    })
  }
  const dayTypes = rules.day_types.map((dayType) => dayType.id)
  repeated(dayTypes, 'day_types')
  for (const [day, dayType] of Object.entries(rules.week)) {
    if (dayType !== undefined && !dayTypes.includes(dayType)) {
      problems.push({
        path: pointer(pointer(at, 'week'), day),
        message: `day type '${dayType}' is not defined in day_types (day_types has: ${dayTypes.join(', ')})`,
      })
    }
  }
  rules.day_types.forEach((dayType, index) => {
    const nutrients = dayType.extra.map((target) => target.nutrient)
    nutrients.forEach((nutrient, place) => {
      const path = pointer(pointer(pointer(pointer(at, 'day_types'), index), 'extra'), place)
      const field = CORE_TARGET_FIELDS[nutrient]
      if (field !== undefined) {
        problems.push({
          path: pointer(path, 'nutrient'),
          message: `'${nutrient}' repeats a target the day type already has; set it in ${field} and take it out of extra`,
        })
      } else if (nutrients.indexOf(nutrient) !== place) {
        problems.push({
          path: pointer(path, 'nutrient'),
          message: `'${nutrient}' is listed more than once; a day type has one extra target for each nutrient`,
        })
      }
    })
  })
  repeated(
    rules.slots.map((slot) => slot.id),
    'slots',
  )
  for (const field of ['kcal_share', 'protein_share'] as const) {
    const total = rules.slots.reduce((sum, slot) => sum + slot[field], 0)
    if (Math.abs(total - 1) > SHARE_SUM_TOLERANCE) {
      problems.push({
        path: pointer(at, 'slots'),
        message: `${field} values must add up to 1, they add up to ${Number(total.toPrecision(4))}`,
      })
    }
  }
  return problems
}

// MealLog's and LogEntry's validators, and the summary's check that each slot is the plan's,
// which the model does not make (see the top of this file).
function logProblems(log: MealLog, rules: PlanRules | null, at = '/log'): FieldProblem[] {
  const problems: FieldProblem[] = []
  const entries = pointer(at, 'entries')
  const slots = rules?.slots.map((slot) => slot.id) ?? null
  const seen = new Set<string>()
  log.entries.forEach((entry, index) => {
    const path = pointer(entries, index)
    const where = `${entry.date} ${entry.slot_id}`
    if (entry.status === 'skipped' && entry.items.length > 0) {
      problems.push({
        path: pointer(path, 'items'),
        message: `${where}: a skipped meal lists no foods`,
      })
    }
    if (entry.status !== 'skipped' && entry.items.length === 0) {
      problems.push({
        path: pointer(path, 'items'),
        message: `${where}: a meal with status '${entry.status}' needs at least one food; mark a meal that was not eaten as 'skipped'`,
      })
    }
    if (seen.has(where)) {
      problems.push({ path, message: `${where} is listed more than once` })
    }
    seen.add(where)
    if (slots !== null && !slots.includes(entry.slot_id)) {
      problems.push({
        path: pointer(path, 'slot_id'),
        message: `${where}: the plan has no slot '${entry.slot_id}'; its slots are ${slots.join(', ')}`,
      })
    }
  })
  if (log.entries.length > 0) {
    const dates = log.entries.map((entry) => entry.date).sort()
    const first = dates[0]!
    const last = dates[dates.length - 1]!
    const days = Math.round((Date.parse(last) - Date.parse(first)) / 86_400_000) + 1
    if (days > LOG_DAYS) {
      problems.push({
        path: entries,
        message: `the dates run from ${first} to ${last}, which is ${days} days; a log covers at most ${LOG_DAYS} consecutive days`,
      })
    }
  }
  return problems
}
