// The words the screens use for the API's codes and keys, and how they show numbers. Numbers
// are only formatted here, never worked out: every one comes from an API response.

import type { Allergen, DayKey, Diet } from './api'
import type { HouseholdMeasure, NutrientTarget, PlanRules, Slot } from './types'

export type Gear = PlanRules['gear'][number]
export type CookWhen = Slot['cook_when']
export type NutrientKey = NutrientTarget['nutrient']

// The days of a plan's week, in order. PlanRules.week has a key for each.
export const DAYS: readonly DayKey[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

export const DAY_NAMES: Record<DayKey, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

export function isDay(value: string): value is DayKey {
  return (DAYS as readonly string[]).includes(value)
}

export const GEAR: Record<Gear, string> = {
  air_fryer: 'Air fryer',
  pressure_cooker: 'Pressure cooker',
  microwave: 'Microwave',
  oven: 'Oven',
  stovetop_pan: 'Stovetop pan',
  kadhai: 'Kadhai',
  blender: 'Blender',
  jar_with_lid: 'Jar with a lid',
  rice_cooker: 'Rice cooker',
  steamer: 'Steamer',
  kettle: 'Kettle',
}

export const COOK_WHEN: Record<CookWhen, string> = {
  fresh: 'Fresh, just before eating',
  evening_before: 'The evening before',
  night_before: 'The night before, nothing to cook that morning',
}

export const DIETS: Record<Diet, string> = {
  veg: 'Vegetarian',
  egg: 'Vegetarian with eggs',
  non_veg: 'Non-vegetarian',
  vegan: 'Vegan',
}

export const ALLERGENS: Record<Allergen, string> = {
  gluten: 'Gluten',
  milk: 'Milk',
  egg: 'Egg',
  peanut: 'Peanut',
  tree_nuts: 'Tree nuts',
  soy: 'Soy',
  fish: 'Fish',
  crustaceans: 'Crustaceans',
  sesame: 'Sesame',
  mustard: 'Mustard',
}

interface NutrientWords {
  name: string
  unit: string
}

// nutrition-mcp's 16 nutrients, in its order.
export const NUTRIENTS: Record<NutrientKey, NutrientWords> = {
  kcal: { name: 'Energy', unit: 'kcal' },
  protein_g: { name: 'Protein', unit: 'g' },
  fat_g: { name: 'Fat', unit: 'g' },
  sat_fat_g: { name: 'Saturated fat', unit: 'g' },
  carbs_available_g: { name: 'Carbohydrate', unit: 'g' },
  sugars_g: { name: 'Sugars', unit: 'g' },
  fibre_g: { name: 'Fibre', unit: 'g' },
  sodium_mg: { name: 'Sodium', unit: 'mg' },
  iron_mg: { name: 'Iron', unit: 'mg' },
  calcium_mg: { name: 'Calcium', unit: 'mg' },
  zinc_mg: { name: 'Zinc', unit: 'mg' },
  folate_ug: { name: 'Folate', unit: 'µg' },
  vit_a_ug: { name: 'Vitamin A', unit: 'µg' },
  vit_b12_ug: { name: 'Vitamin B12', unit: 'µg' },
  vit_c_mg: { name: 'Vitamin C', unit: 'mg' },
  vit_d_ug: { name: 'Vitamin D', unit: 'µg' },
}

export const NUTRIENT_KEYS = Object.keys(NUTRIENTS) as NutrientKey[]

// The targets a day type has a field of its own for; an extra target names one of the rest.
export const CORE_NUTRIENTS: readonly NutrientKey[] = [
  'kcal',
  'protein_g',
  'sat_fat_g',
  'fibre_g',
  'carbs_available_g',
  'fat_g',
]

export const EXTRA_NUTRIENTS = NUTRIENT_KEYS.filter((key) => !CORE_NUTRIENTS.includes(key))

// A number as the screens show it: whole numbers from 10 up, one decimal below that, and no
// trailing zero. Grouping follows the reader's locale.
export function shown(value: number): string {
  const digits = Math.abs(value) >= 10 ? 0 : 1
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

// A measure's amount is a whole number of quarters (src/meal_planner/measures.py).
const QUARTERS: Record<number, string> = { 0.25: '¼', 0.5: '½', 0.75: '¾' }

// The plural of each unit in nutrition-mcp's data and the catalogue that has one. tsp and tbsp
// are abbreviations and stay as they are, and so does a unit not listed here.
const PLURALS: Record<string, string> = {
  piece: 'pieces',
  roti: 'rotis',
  clove: 'cloves',
  leaf: 'leaves',
  slice: 'slices',
  cup: 'cups',
  glass: 'glasses',
  scoop: 'scoops',
  katori: 'katoris',
  plate: 'plates',
  serving: 'servings',
}

// A household measure as a kitchen says it: "1½ cups", "¾ tbsp", "2 pieces". The amount and the
// unit are the API's; only the words are made here.
export function measureWords({ amount, unit }: HouseholdMeasure): string {
  const whole = Math.floor(amount)
  const quarter = QUARTERS[amount - whole]
  let number = shown(amount)
  if (amount === whole) number = String(whole)
  else if (quarter !== undefined) number = whole === 0 ? quarter : `${whole}${quarter}`
  return `${number} ${amount > 1 ? (PLURALS[unit] ?? unit) : unit}`
}

// The proper adjectives that start a name in data/catalogue.yaml ("Greek yogurt (low fat)",
// "French beans"). A name that starts with one keeps its capital inside a sentence.
const PROPER_ADJECTIVES: readonly string[] = ['Greek', 'French']

// A catalogue name as it reads at `at` in a sentence: as written at the start, and otherwise in
// lower case unless it starts with a proper adjective.
function inSentence(name: string, at: number): string {
  if (at === 0 || PROPER_ADJECTIVES.includes(name.split(' ')[0]!)) return name
  return name.charAt(0).toLowerCase() + name.slice(1)
}

// Turns a step that quotes catalogue ids ("cook the brown_rice") into one with the catalogue's
// names for those foods ("cook the brown rice"), whole words only. Only ids of more than one
// word, joined by "_", are replaced: an id of one word ("curd") is already a word of the step and
// stays as written. The pattern is built once for the names given. For showing only: the step
// the week keeps is unchanged.
export function stepNamer(names: ReadonlyMap<string, string>): (step: string) => string {
  const ids = [...names.keys()]
    .filter((id) => id.includes('_'))
    .map((id) => id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (ids.length === 0) return (step) => step
  const quoted = new RegExp(`\\b(?:${ids.join('|')})\\b`, 'g')
  return (step) => step.replace(quoted, (id, at: number) => inSentence(names.get(id) ?? id, at))
}

// The codes a slot's failure can carry, in words. Any other reason is already a sentence.
const REASONS: Record<string, string> = {
  rate_limited: "the model's rate limit was reached",
  timeout: 'the model did not answer in time',
  server_error: "the model's server had an error",
  model_error: 'the model call failed',
  no_reply: 'the model did not reply',
  no_recording: 'no recorded reply matches this request',
  invalid_json: 'the reply could not be read',
  schema_mismatch: 'the reply did not have the recipes in the expected form',
  wrong_recipes: 'the reply did not have the recipes asked for',
}

export function reasonWords(reason: string): string {
  return REASONS[reason] ?? reason
}

// A message from the API or the storage, which starts in lower case, as a sentence of its own.
export function sentence(text: string): string {
  return text.length === 0 ? text : text[0]!.toUpperCase() + text.slice(1)
}

// What went wrong, in the error's own words. A browser's storage and download errors are
// DOMExceptions, which are not always Error objects, so anything with a message gives it.
export function messageOf(error: unknown): string {
  const message = (error as { message?: unknown } | null)?.message
  return typeof message === 'string' ? message : String(error)
}
