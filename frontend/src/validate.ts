// Checks that a file a person loads, or what this browser saved before, is data the server
// accepts: first against schemas/export.json, then the rules a JSON Schema cannot say, which the
// server's models check in code (src/meal_planner/rules.py and summary.py). Each problem names
// its place as a JSON Pointer, such as /log/entries/0/items/0/food_id, so a person can find it.

import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020'

import exportSchema from '../../schemas/export.json'
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

// The formats as Pydantic reads them: a date on the calendar, and a time with or without
// seconds (a plan says "08:00"), with an optional offset.
function isDate(text: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (match === null) return false
  const [year, month, day] = match.slice(1).map(Number) as [number, number, number]
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  )
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d{1,6})?)?(Z|[+-]([01]\d|2[0-3]):?[0-5]\d)?$/

// The problems Ajv finds in some data; none when it fits.
type Check = (data: unknown) => ErrorObject[]

function errorsOf(validate: ValidateFunction): Check {
  return (data) => (validate(data) ? [] : (validate.errors ?? []))
}

let checks: { exported: Check; stored: Check } | null = null

// Compiled on first use, since it takes a moment and most page loads never import a file.
function compiled(): { exported: Check; stored: Check } {
  if (checks !== null) return checks
  // useDefaults fills in a field the server has a default for, so the data matches its type.
  const ajv = new Ajv2020({ allErrors: true, useDefaults: true, strict: true })
  ajv.addFormat('date', { type: 'string', validate: isDate })
  ajv.addFormat('time', { type: 'string', validate: (text: string) => TIME.test(text) })
  // Added under a name, so that the schema for saved data can refer to its parts.
  ajv.addSchema(exportSchema, 'export.json')
  const exported = ajv.getSchema('export.json') as ValidateFunction | undefined
  if (exported === undefined) throw new Error('schemas/export.json did not compile')
  checks = {
    exported: errorsOf(exported),
    stored: errorsOf(
      ajv.compile({
        type: 'object',
        additionalProperties: false,
        required: ['version', 'rules', 'week', 'log'],
        properties: {
          version: { const: 1 },
          rules: { anyOf: [{ $ref: 'export.json#/$defs/PlanRules' }, { type: 'null' }] },
          week: { $ref: 'export.json#/properties/week' },
          log: { $ref: 'export.json#/$defs/MealLog' },
        },
      }),
    ),
  }
  return checks
}

// An export file as it was saved, checked. Throws ValidationFailed with every problem found.
export function checkExport(data: unknown): Export {
  throwIfAny(schemaProblems(compiled().exported(data)))
  const file = data as Export
  throwIfAny([...ruleProblems(file.rules), ...logProblems(file.log, file.rules)])
  return file
}

// What this browser saved, checked the same way; it may have no plan rules yet.
export function checkStored(data: unknown): State & { version: 1 } {
  throwIfAny(schemaProblems(compiled().stored(data)))
  const state = data as State & { version: 1 }
  const rules = state.rules === null ? [] : ruleProblems(state.rules)
  throwIfAny([...rules, ...logProblems(state.log, state.rules)])
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
      return { path: at, message: error.message ?? 'is not valid' }
  }
}

// One problem per place. A value that may be null ("week": a week or null) fails both ways, and
// an "anyOf" error says only that; the deepest problem is the one that says what to fix.
function schemaProblems(errors: ErrorObject[]): FieldProblem[] {
  const problems = errors
    .filter((error) => error.keyword !== 'anyOf' && error.keyword !== 'oneOf')
    .map(described)
  const deeper = (a: string, b: string): boolean => b.startsWith(`${a}/`)
  const kept = problems.filter(
    (problem) => !problems.some((other) => deeper(problem.path, other.path)),
  )
  const seen = new Set<string>()
  return kept.filter((problem) => {
    const key = `${problem.path} ${problem.message}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
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

// MealLog's and LogEntry's validators, and the summary's check that each slot is the plan's.
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
