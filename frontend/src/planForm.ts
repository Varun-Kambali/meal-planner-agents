// What the plan form edits, and how it becomes PlanRules. The form keeps what the person typed
// as text and turns it into numbers only when the plan is saved, so a half-typed number is never
// lost or rounded. Problems, the form's own and the server's, are placed at a field by a dotted
// path relative to the rules, such as "slots.0.kcal_share"; "" is the plan as a whole.

import type { Allergen, DayKey, Diet } from './api'
import type { DayType, MealLog, NutrientTarget, PlanRules, Problem, Slot } from './types'
import { ValidationFailed, checkStored, type FieldProblem } from './validate'
import { DAYS, DAY_NAMES, type CookWhen, type Gear, type NutrientKey } from './words'

export interface ExtraDraft {
  key: string
  nutrient: NutrientKey | ''
  kind: NutrientTarget['kind']
  value: string
}

// The six daily targets every day type has, in the order the form shows them.
export const TARGET_FIELDS = [
  'kcal',
  'protein_g',
  'carbs_g',
  'fat_g',
  'sat_fat_g_max',
  'fibre_g_min',
] as const
export type TargetField = (typeof TARGET_FIELDS)[number]

export interface DayTypeDraft extends Record<TargetField, string> {
  key: string
  // Kept from the saved rules, so that renaming a day type does not change what the week and
  // the log refer to. A new day type gets one from its name when the plan is saved.
  id: string | null
  name: string
  extra: ExtraDraft[]
}

export interface SlotDraft {
  key: string
  id: string | null
  name: string
  eat_at: string
  // Percentages of the day, as typed: "25" is a quarter.
  kcal_share: string
  protein_share: string
  style: string
  cook_when: CookWhen
  max_active_min: string
  max_total_min: string
}

export interface PlanDraft {
  name: string
  dayTypes: DayTypeDraft[]
  // Each day's day type, by the draft's key for it.
  week: Record<DayKey, string>
  slots: SlotDraft[]
  gear: Gear[]
  diet: Diet | ''
  allergens: Allergen[]
  excludeFoods: string[]
  likedFoods: string[]
  // One cuisine per line.
  cuisines: string
  shoppingDays: DayKey[]
}

export interface FormError {
  path: string
  message: string
}

export const MAX_DAY_TYPES = 4
export const MAX_SLOTS = 8
export const MAX_CUISINES = 10

let made = 0
export function newKey(): string {
  made += 1
  return `row-${made}`
}

export function emptyExtra(): ExtraDraft {
  return { key: newKey(), nutrient: '', kind: 'min', value: '' }
}

export function emptyDayType(): DayTypeDraft {
  return {
    key: newKey(),
    id: null,
    name: '',
    kcal: '',
    protein_g: '',
    carbs_g: '',
    fat_g: '',
    sat_fat_g_max: '',
    fibre_g_min: '',
    extra: [],
  }
}

export function emptySlot(): SlotDraft {
  return {
    key: newKey(),
    id: null,
    name: '',
    eat_at: '',
    kcal_share: '',
    protein_share: '',
    style: '',
    cook_when: 'fresh',
    max_active_min: '',
    max_total_min: '',
  }
}

// A share of the day (0.25) as the percentage the form shows (25), without float noise.
function percent(share: number): string {
  return String(Math.round(share * 1000) / 10)
}

export function draftOf(rules: PlanRules | null): PlanDraft {
  if (rules === null) {
    const dayType = emptyDayType()
    return {
      name: '',
      dayTypes: [dayType],
      week: Object.fromEntries(DAYS.map((day) => [day, dayType.key])) as Record<DayKey, string>,
      slots: [emptySlot()],
      gear: [],
      diet: '',
      allergens: [],
      excludeFoods: [],
      likedFoods: [],
      cuisines: '',
      shoppingDays: [],
    }
  }
  const dayTypes = rules.day_types.map((dayType): DayTypeDraft => ({
    key: newKey(),
    id: dayType.id,
    name: dayType.name,
    kcal: String(dayType.kcal),
    protein_g: String(dayType.protein_g),
    carbs_g: String(dayType.carbs_g),
    fat_g: String(dayType.fat_g),
    sat_fat_g_max: String(dayType.sat_fat_g_max),
    fibre_g_min: String(dayType.fibre_g_min),
    extra: dayType.extra.map((target) => ({
      key: newKey(),
      nutrient: target.nutrient,
      kind: target.kind,
      value: String(target.value),
    })),
  }))
  const keyOf = (id: string): string =>
    dayTypes.find((dayType) => dayType.id === id)?.key ?? dayTypes[0]!.key
  return {
    name: rules.name,
    dayTypes,
    week: Object.fromEntries(DAYS.map((day) => [day, keyOf(rules.week[day])])) as Record<
      DayKey,
      string
    >,
    slots: rules.slots.map((slot) => ({
      key: newKey(),
      id: slot.id,
      name: slot.name,
      eat_at: slot.eat_at,
      kcal_share: percent(slot.kcal_share),
      protein_share: percent(slot.protein_share),
      style: slot.style,
      cook_when: slot.cook_when,
      max_active_min: String(slot.max_active_min),
      max_total_min: String(slot.max_total_min),
    })),
    gear: [...rules.gear],
    diet: rules.food.diet,
    allergens: [...rules.food.exclude_allergens],
    excludeFoods: [...rules.food.exclude_foods],
    likedFoods: [...rules.food.liked_foods],
    cuisines: rules.food.cuisines.join('\n'),
    // Lists keep the order they were saved in, so an unchanged plan saves as it was.
    shoppingDays: [...rules.shopping_days],
  }
}

// The lines of the cuisines box that have something on them.
export function cuisineLines(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
}

// An id the server accepts (^[a-z][a-z0-9_]{0,29}$) made from a name, unique among `taken`.
function idFrom(name: string, fallback: string, taken: Set<string>): string {
  const base =
    name
      .normalize('NFKD')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^[^a-z]+/, '')
      .replace(/_+$/, '')
      .slice(0, 26) || fallback
  let id = base
  for (let n = 2; taken.has(id); n += 1) id = `${base}_${n}`
  taken.add(id)
  return id
}

function ids(rows: { id: string | null; name: string }[], fallback: string): string[] {
  const taken = new Set(rows.flatMap((row) => (row.id === null ? [] : [row.id])))
  return rows.map((row, index) => row.id ?? idFrom(row.name, `${fallback}_${index + 1}`, taken))
}

type Parsed = { ok: true; value: number } | { ok: false }

function parse(text: string, whole = false): Parsed {
  const trimmed = text.trim()
  const pattern = whole ? /^\d+$/ : /^\d+(\.\d+)?$|^\.\d+$/
  if (!pattern.test(trimmed)) return { ok: false }
  return { ok: true, value: Number(trimmed) }
}

// Slot shares may miss a whole day by this much, as on the server.
const SHARE_SUM_TOLERANCE = 0.001

function shareTotal(shares: number[]): number {
  return shares.reduce((sum, share) => sum + share, 0)
}

const SHARE_NAMES = { kcal_share: 'Calorie', protein_share: 'Protein' } as const

function shareSumMessage(field: keyof typeof SHARE_NAMES, total: number): string {
  const shown = Math.round(total * 1000) / 10
  return `${SHARE_NAMES[field]} shares add up to ${shown}% of the day; they must add up to 100%.`
}

export type Built = { rules: PlanRules; errors: [] } | { rules: null; errors: FormError[] }

// The rules the draft describes, or every problem found. The form's own checks come first, in
// words about what was typed; then the checks the import and the server make (validate.ts),
// against the rules and the meal log they will be saved with.
export function buildRules(draft: PlanDraft, log: MealLog): Built {
  const errors: FormError[] = []
  const add = (path: string, message: string): void => {
    errors.push({ path, message })
  }
  const number = (path: string, text: string, whole = false): number => {
    const parsed = parse(text, whole)
    if (parsed.ok) return parsed.value
    if (text.trim() === '') add(path, whole ? 'Enter a number of minutes.' : 'Enter a number.')
    else add(path, whole ? 'Enter a whole number of minutes.' : 'Enter a number, such as 2650.')
    return Number.NaN
  }
  const named = (path: string, text: string, what: string): void => {
    if (text.trim() === '') add(path, `Give the ${what} a name.`)
  }

  named('name', draft.name, 'plan')
  const dayTypeIds = ids(draft.dayTypes, 'day')
  const dayTypes = draft.dayTypes.map((row, i): DayType => {
    const at = `day_types.${i}`
    named(`${at}.name`, row.name, 'day type')
    const targets = Object.fromEntries(
      TARGET_FIELDS.map((field) => [field, number(`${at}.${field}`, row[field])]),
    ) as Record<TargetField, number>
    const extra = row.extra.map((target, j): NutrientTarget => {
      if (target.nutrient === '') add(`${at}.extra.${j}.nutrient`, 'Choose a nutrient.')
      return {
        nutrient: target.nutrient as NutrientKey,
        kind: target.kind,
        value: number(`${at}.extra.${j}.value`, target.value),
      }
    })
    return { id: dayTypeIds[i]!, name: row.name.trim(), ...targets, extra }
  })
  const idOfKey = new Map(draft.dayTypes.map((row, i) => [row.key, dayTypeIds[i]!]))

  const slotIds = ids(draft.slots, 'meal')
  const slots = draft.slots.map((row, i): Slot => {
    const at = `slots.${i}`
    named(`${at}.name`, row.name, 'meal')
    if (row.eat_at.trim() === '') add(`${at}.eat_at`, 'Enter the time this meal is eaten.')
    const share = (field: 'kcal_share' | 'protein_share'): number => {
      const parsed = parse(row[field])
      const least = field === 'kcal_share' ? 'above 0%' : 'from 0%'
      if (!parsed.ok || parsed.value > 100 || (field === 'kcal_share' && parsed.value === 0)) {
        add(`${at}.${field}`, `Enter a percentage ${least} up to 100%.`)
        return Number.NaN
      }
      return parsed.value / 100
    }
    const kcalShare = share('kcal_share')
    const proteinShare = share('protein_share')
    if (row.style.trim() === '') add(`${at}.style`, 'Say what kind of dish it is.')
    return {
      id: slotIds[i]!,
      name: row.name.trim(),
      eat_at: row.eat_at.trim(),
      kcal_share: kcalShare,
      protein_share: proteinShare,
      style: row.style.trim(),
      cook_when: row.cook_when,
      max_active_min: number(`${at}.max_active_min`, row.max_active_min, true),
      max_total_min: number(`${at}.max_total_min`, row.max_total_min, true),
    }
  })
  for (const field of ['kcal_share', 'protein_share'] as const) {
    const total = shareTotal(slots.map((slot) => slot[field]))
    if (!Number.isNaN(total) && Math.abs(total - 1) > SHARE_SUM_TOLERANCE) {
      add('slots', shareSumMessage(field, total))
    }
  }

  if (draft.gear.length === 0) add('gear', 'Choose at least one thing you cook with.')
  if (draft.diet === '') add('food.diet', 'Choose a diet.')
  const cuisines = cuisineLines(draft.cuisines)
  if (cuisines.length === 0) add('food.cuisines', 'Name at least one cuisine.')
  if (cuisines.length > MAX_CUISINES) {
    add('food.cuisines', `Name at most ${MAX_CUISINES} cuisines; there are ${cuisines.length}.`)
  }
  if (draft.shoppingDays.length === 0) add('shopping_days', 'Choose at least one day.')
  if (errors.length > 0) return { rules: null, errors }

  const rules: PlanRules = {
    version: 1,
    name: draft.name.trim(),
    day_types: dayTypes,
    week: Object.fromEntries(
      DAYS.map((day) => [day, idOfKey.get(draft.week[day]) ?? dayTypeIds[0]!]),
    ) as PlanRules['week'],
    slots,
    gear: [...draft.gear],
    food: {
      diet: draft.diet as Diet,
      exclude_allergens: [...draft.allergens],
      exclude_foods: [...draft.excludeFoods],
      liked_foods: [...draft.likedFoods],
      cuisines,
    },
    shopping_days: [...draft.shoppingDays],
  }
  try {
    checkStored({ version: 1, rules, week: null, log })
  } catch (error) {
    if (!(error instanceof ValidationFailed)) throw error
    return { rules: null, errors: fromStored(error.problems) }
  }
  return { rules, errors: [] }
}

// The problems validate.ts finds in a stored state, placed in the form. A log entry for a slot
// the plan no longer has belongs to the slots.
export function fromStored(problems: FieldProblem[]): FormError[] {
  return problems.map((problem) => {
    if (problem.path.startsWith('/rules')) {
      const parts = problem.path.slice('/rules'.length).split('/').filter(Boolean)
      return placed(parts.map(unescape), problem.message)
    }
    if (problem.path.startsWith('/log')) {
      return { path: 'slots', message: `Your meal log does not fit this plan: ${problem.message}.` }
    }
    return { path: '', message: problem.message }
  })
}

function unescape(part: string): string {
  return part.replaceAll('~1', '/').replaceAll('~0', '~')
}

// The server's validation problems about the rules, placed in the form. `under` is where the
// rules sit in the request: ["body"] for a new week, ["body", "rules"] for a replacement.
// Problems about anything else in the request are left out.
export function fromServer(problems: Problem[], under: readonly string[]): FormError[] {
  return problems.flatMap((problem) => {
    const loc = problem.loc.map(String)
    if (!under.every((part, i) => loc[i] === part)) return []
    let message = problem.msg.replace(/^Value error, /, '')
    let parts = loc.slice(under.length)
    // A check on the rules as a whole names its field first: "slots: ids must be unique".
    const field = /^(day_types|week|slots|extra): (.*)$/s.exec(message)
    if (field !== null) {
      if (parts.length === 0) parts = [field[1]!]
      message = field[2]!
    }
    const shares =
      /^(kcal_share|protein_share) values must add up to 1, they add up to (\S+)$/.exec(message)
    if (shares !== null) {
      message = shareSumMessage(shares[1] as keyof typeof SHARE_NAMES, Number(shares[2]))
    }
    return [placed(parts, message)]
  })
}

// A cuisine is a line of one box, so a problem with one names its line.
function placed(parts: string[], message: string): FormError {
  if (parts[0] === 'food' && parts[1] === 'cuisines' && parts[2] !== undefined) {
    return { path: 'food.cuisines', message: `Line ${Number(parts[2]) + 1}: ${message}` }
  }
  return { path: parts.join('.'), message }
}

// Whether two plans say the same, whatever order their keys were written in.
export function sameRules(a: PlanRules | null, b: PlanRules | null): boolean {
  return canonical(a) === canonical(b)
}

function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, inner: unknown) =>
    inner !== null && typeof inner === 'object' && !Array.isArray(inner)
      ? Object.fromEntries(
          Object.entries(inner as Record<string, unknown>).sort(([x], [y]) => x.localeCompare(y)),
        )
      : inner,
  )
}

export const TARGET_LABELS: Record<TargetField, string> = {
  kcal: 'Calories (kcal)',
  protein_g: 'Protein, at least (g)',
  carbs_g: 'Carbohydrate (g)',
  fat_g: 'Fat (g)',
  sat_fat_g_max: 'Saturated fat, at most (g)',
  fibre_g_min: 'Fibre, at least (g)',
}

export const SLOT_LABELS: Record<Exclude<keyof SlotDraft, 'key' | 'id'>, string> = {
  name: 'Name',
  eat_at: 'Eaten at',
  kcal_share: "Share of the day's calories (%)",
  protein_share: "Share of the day's protein (%)",
  style: 'Kind of dish',
  cook_when: 'When it is cooked',
  max_active_min: 'Most active minutes',
  max_total_min: 'Most minutes in all',
}

export const FIELD_LABELS: Record<string, string> = {
  name: 'Plan name',
  day_types: 'Day types',
  week: 'The week',
  slots: 'Meals of the day',
  gear: 'Kitchen gear you own',
  'food.diet': 'Diet',
  'food.exclude_allergens': 'Allergens to leave out',
  'food.exclude_foods': 'Foods to leave out',
  'food.liked_foods': 'Foods you like',
  'food.cuisines': 'Cuisines to draw on',
  shopping_days: 'Shopping days',
}

// What a problem's path is called in the list at the top of the form.
export function labelOf(path: string): string {
  if (path in FIELD_LABELS) return FIELD_LABELS[path]!
  const parts = path.split('.')
  if (parts[0] === 'week' && parts[1] !== undefined) {
    return `The week, ${DAY_NAMES[parts[1] as keyof typeof DAY_NAMES] ?? parts[1]}`
  }
  if (parts[0] === 'day_types' && parts[1] !== undefined) {
    const day = `Day type ${Number(parts[1]) + 1}`
    if (parts[2] === undefined) return day
    if (parts[2] === 'name') return `${day}, name`
    if (parts[2] === 'extra') {
      if (parts[3] === undefined) return `${day}, other daily targets`
      return `${day}, other target ${Number(parts[3]) + 1}`
    }
    return `${day}, ${TARGET_LABELS[parts[2] as TargetField]?.toLowerCase() ?? parts[2]}`
  }
  if (parts[0] === 'slots' && parts[1] !== undefined) {
    const meal = `Meal ${Number(parts[1]) + 1}`
    if (parts[2] === undefined) return meal
    const label = SLOT_LABELS[parts[2] as keyof typeof SLOT_LABELS]
    return `${meal}, ${label?.toLowerCase() ?? parts[2]}`
  }
  return ''
}
