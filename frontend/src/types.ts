// Generated from schemas/ by frontend/scripts/generate-types.ts. Do not edit by hand:
// change the server's models, run `uv run python scripts/export_schemas.py`, then
// `npm run types` in frontend/.

export type Catalogue = CatalogueRow[]
export type FoodSearchResult = FoodOption[]
export type StreamEvent =
  SlotStarted | SlotWritten | RecipeChecked | RepairStarted | SlotFailed | WeekDone | StreamError

/**
 * Everything the browser keeps, as the one file a person saves and loads again.
 */
export interface Export {
  version: 1
  rules: PlanRules
  week: WeekResult | null
  log: MealLog
}
/**
 * Everything a user enters about the plan they follow.
 */
export interface PlanRules {
  version: 1
  name: string
  /**
   * @minItems 1
   * @maxItems 4
   */
  day_types: DayType[]
  week: {
    [k: string]: string | undefined
  }
  /**
   * @minItems 1
   * @maxItems 8
   */
  slots: Slot[]
  /**
   * @minItems 1
   */
  gear: (
    | 'air_fryer'
    | 'pressure_cooker'
    | 'microwave'
    | 'oven'
    | 'stovetop_pan'
    | 'kadhai'
    | 'blender'
    | 'jar_with_lid'
    | 'rice_cooker'
    | 'steamer'
    | 'kettle'
  )[]
  food: FoodRules
  /**
   * @minItems 1
   * @maxItems 7
   */
  shopping_days: ('mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun')[]
}
/**
 * One kind of day, such as a training day, with the daily targets it is held to.
 */
export interface DayType {
  id: string
  name: string
  kcal: number
  protein_g: number
  carbs_g: number
  fat_g: number
  sat_fat_g_max: number
  fibre_g_min: number
  extra: NutrientTarget[]
}
/**
 * A daily target for one nutrient: at least `value` (min) or at most `value` (max).
 */
export interface NutrientTarget {
  nutrient:
    | 'kcal'
    | 'protein_g'
    | 'fat_g'
    | 'sat_fat_g'
    | 'carbs_available_g'
    | 'sugars_g'
    | 'fibre_g'
    | 'sodium_mg'
    | 'iron_mg'
    | 'calcium_mg'
    | 'zinc_mg'
    | 'folate_ug'
    | 'vit_a_ug'
    | 'vit_b12_ug'
    | 'vit_c_mg'
    | 'vit_d_ug'
  kind: 'min' | 'max'
  value: number
}
/**
 * One meal of the day, with its share of the day's calories and protein.
 */
export interface Slot {
  id: string
  name: string
  eat_at: string
  kcal_share: number
  protein_share: number
  style: string
  cook_when: 'fresh' | 'evening_before' | 'night_before'
  max_active_min: number
  max_total_min: number
}
/**
 * Diet, allergens and foods to avoid or favour, and the cuisines to draw on.
 */
export interface FoodRules {
  diet: 'veg' | 'egg' | 'non_veg' | 'vegan'
  exclude_allergens: (
    | 'gluten'
    | 'milk'
    | 'egg'
    | 'peanut'
    | 'tree_nuts'
    | 'soy'
    | 'fish'
    | 'crustaceans'
    | 'sesame'
    | 'mustard'
  )[]
  exclude_foods: string[]
  liked_foods: string[]
  /**
   * @minItems 1
   * @maxItems 10
   */
  cuisines: string[]
}
/**
 * The week as the graph leaves it: every recipe with its numbers, and what still fails.
 */
export interface WeekResult {
  recipes: CheckedRecipe[]
  days: DayReport[]
  no_waste: NoWasteRow[]
  unwritten: ['mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun', string, string][]
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
}
/**
 * A draft with its numbers from nutrition-mcp and the checks it has failed so far.
 */
export interface CheckedRecipe {
  spec: SlotSpec
  draft: DraftRecipe
  nutrition: MealResult | null
  failures: Failure[]
  attempts: number
  portion_scale: number
}
/**
 * One slot on one day, with the calories and protein its share of that day type comes to.
 */
export interface SlotSpec {
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  day_type: string
  slot: Slot
  kcal: number
  protein_g: number
}
/**
 * A recipe as the model writes it. It carries no nutrition numbers: those come from code.
 */
export interface DraftRecipe {
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  title: string
  cuisine: string
  /**
   * @minItems 1
   * @maxItems 15
   */
  ingredients: DraftIngredient[]
  /**
   * @minItems 1
   * @maxItems 10
   */
  steps: string[]
  active_min: number
  total_min: number
  gear: (
    | 'air_fryer'
    | 'pressure_cooker'
    | 'microwave'
    | 'oven'
    | 'stovetop_pan'
    | 'kadhai'
    | 'blender'
    | 'jar_with_lid'
    | 'rice_cooker'
    | 'steamer'
    | 'kettle'
  )[]
  cook_when: 'fresh' | 'evening_before' | 'night_before'
  store: string
  swap: string
  fits: string
}
/**
 * One catalogue ingredient and how much of it, in grams.
 */
export interface DraftIngredient {
  id: string
  grams: number
}
export interface MealResult {
  items: NutritionResult[]
  total: Nutrients
  total_incomplete: {
    [k: string]: string[] | undefined
  }
  glycemic_load: number | null
  glycemic_load_missing: string[]
}
/**
 * The nutrients in one amount of one food, with how the amount was worked out.
 */
export interface NutritionResult {
  food: FoodSummary
  amount: number
  unit: string
  grams: number
  measure_assumption: string
  nutrients: Nutrients
  incomplete: {
    [k: string]: string[] | undefined
  }
  raw_ingredient_totals: string[]
  gi: number | null
  gi_ref: string | null
  glycemic_load: number | null
  oil_assumption: string | null
  sources: string[]
}
/**
 * Enough to identify a food and to pick a unit that fits it.
 */
export interface FoodSummary {
  id: string
  kind: 'ingredient' | 'dish'
  name: string
  aliases: string[]
  regions: ('north' | 'south' | 'east' | 'west')[]
  diet: 'veg' | 'egg' | 'non_veg' | 'vegan'
  source_ids: string[]
  /**
   * Every unit get_nutrition and compute_meal accept for this food, with the grams in one of each: g, the food's own measures, then the default katori for a dish served by the bowl and the volume units for a liquid.
   */
  units: {
    [k: string]: number | undefined
  }
}
/**
 * Amounts of the 16 nutrients; null means not reported, which is not 0.
 */
export interface Nutrients {
  kcal: number | null
  protein_g: number | null
  fat_g: number | null
  sat_fat_g: number | null
  carbs_available_g: number | null
  sugars_g: number | null
  fibre_g: number | null
  sodium_mg: number | null
  iron_mg: number | null
  calcium_mg: number | null
  zinc_mg: number | null
  folate_ug: number | null
  vit_a_ug: number | null
  vit_b12_ug: number | null
  vit_c_mg: number | null
  vit_d_ug: number | null
}
/**
 * A rule a recipe or day misses, in words the writer can act on.
 */
export interface Failure {
  check: string
  message: string
}
/**
 * A day's totals against its day type. Keys are nutrition-mcp's nutrient names.
 */
export interface DayReport {
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  totals: {
    [k: string]: number | null | undefined
  }
  failures: Failure[]
  unsettled: Failure[]
  soft: {
    [k: string]: number | undefined
  }
}
export interface NoWasteRow {
  ingredient: string
  window_start: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  failure: Failure
}
/**
 * What was eaten over at most seven consecutive days, one entry for each slot of a date.
 */
export interface MealLog {
  version: 1
  entries: LogEntry[]
}
/**
 * One planned meal, on a date: how it went and what was eaten.
 *
 * A skipped meal lists nothing. Any other lists the foods that were eaten, at most as many as
 * one typed meal can hold.
 */
export interface LogEntry {
  date: string
  slot_id: string
  status: 'as_planned' | 'different_amount' | 'swapped' | 'skipped'
  /**
   * @maxItems 20
   */
  items: LogItem[]
}
/**
 * One food that was eaten: an id from nutrition-mcp, how many of a unit, and the unit.
 */
export interface LogItem {
  food_id: string
  amount: number
  unit: string
}
export interface CatalogueRow {
  id: string
  name: string
  category:
    | 'grain'
    | 'pulse'
    | 'dairy'
    | 'egg'
    | 'meat'
    | 'fish'
    | 'veg'
    | 'fruit'
    | 'nut_seed'
    | 'oil'
    | 'spice'
    | 'sauce'
    | 'sweetener'
    | 'other'
}
/**
 * The foods of one meal as the log holds them: ids, amounts and units, and no number.
 */
export interface ComputeRequest {
  /**
   * @minItems 1
   * @maxItems 20
   */
  items: LogItem[]
}
/**
 * One date of the log against the targets of its day type.
 *
 * `totals`, `failures` and `unsettled` come from the functions the day checks use, so they read
 * the same. A total is None when no food eaten has a value for the nutrient. The two dicts by
 * slot cover the meals of the day that were eaten.
 */
export interface DaySummary {
  date: string
  day_type: string
  totals: {
    [k: string]: number | null | undefined
  }
  targets: {
    [k: string]: number | undefined
  }
  failures: Failure[]
  unsettled: Failure[]
  soft: {
    [k: string]: number | undefined
  }
  glycemic_load: {
    [k: string]: number | null | undefined
  }
  gl_missing: {
    [k: string]: number | undefined
  }
}
/**
 * A food a name found, and the units an amount of it can be given in.
 */
export interface FoodOption {
  id: string
  name: string
  kind: 'ingredient' | 'dish'
  units: string[]
}
export interface Problem {
  loc: (string | number)[]
  msg: string
}
/**
 * One food of the meal and what became of it.
 *
 * `matched`: the food and a usable amount were found, and `grams` and `nutrition` are set.
 * `needs_amount`: the food was found but the amount is missing, vague, a range, not the number
 * its words give, or beyond the limit; `valid_units` lists what could be used to give one.
 * `unknown_unit`: the food was found but the unit is not one nutrition-mcp or the catalogue has
 * for it; `valid_units` lists those that are. `missing`: the food is not in nutrition-mcp's
 * data.
 */
export interface ReadItem {
  name: string
  food_id: string | null
  food_name: string | null
  amount: number | null
  unit: string | null
  grams: number | null
  status: 'matched' | 'needs_amount' | 'unknown_unit' | 'missing'
  valid_units: string[]
  nutrition: NutritionResult | null
}
export interface ReadRequest {
  text: string
}
export interface ReadResult {
  text_length: number
  items: ReadItem[]
  total: MealResult | null
  missing: string[]
}
/**
 * A recipe was checked, or its failures changed. `attempt` counts this recipe's tries.
 */
export interface RecipeChecked {
  kind: 'recipe_checked'
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  slot_id: string
  attempt: number
  failures: Failure[]
}
/**
 * A model call to rewrite a slot's failing recipes has begun.
 */
export interface RepairStarted {
  kind: 'repair_started'
  slot_id: string
  days: ('mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun')[]
  attempt: number
}
/**
 * A recipe of the week as the model wrote it.
 *
 * There is no field for its numbers or its failures: the server works those out again, because
 * anything the browser echoes back could have been changed on the way.
 */
export interface ReplaceEntry {
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  slot_id: string
  draft: DraftRecipe
  attempts: number
}
export interface ReplaceRequest {
  rules: PlanRules
  /**
   * @maxItems 56
   */
  recipes: ReplaceEntry[]
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'
  slot_id: string
}
/**
 * A model call gave nothing usable. `final` is false when the slot will be tried again.
 */
export interface SlotFailed {
  kind: 'slot_failed'
  slot_id: string
  days: ('mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun')[]
  attempt: number
  reason: string
  final: boolean
}
/**
 * A model call to write recipes for a slot has begun. `attempt` counts the slot's calls.
 */
export interface SlotStarted {
  kind: 'slot_started'
  slot_id: string
  days: ('mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun')[]
  attempt: number
}
/**
 * The model's reply for the slot had the recipes asked for. They have not been checked.
 */
export interface SlotWritten {
  kind: 'slot_written'
  slot_id: string
  days: ('mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun')[]
  attempt: number
}
/**
 * Sent instead of `week_done` when a run fails after the stream has started.
 *
 * It names the step and says only that the run stopped: the exception's text can quote the
 * request, so none of it is sent.
 */
export interface StreamError {
  kind: 'error'
  step: 'week' | 'replace'
  message: string
}
/**
 * The last event of every run.
 */
export interface WeekDone {
  kind: 'week_done'
  week: WeekResult
}
export interface SummaryRequest {
  rules: PlanRules
  log: MealLog
}
/**
 * The days of a log, and how much of the plan was followed.
 */
export interface WeekSummary {
  days: DaySummary[]
  as_planned_share: number
  days_on_target: number
  days_unsettled: number
  planned_meals: number
}
