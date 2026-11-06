// What the tracking screens' tests share: replies of the meal reader, the compute route and the
// summary, made by hand in the shapes the API returns. The numbers are made up for the tests and
// are never added up here, so a test can tell that a number on the screen is the server's.

import type {
  DaySummary,
  MealResult,
  Nutrients,
  NutritionResult,
  ReadItem,
  ReadResult,
  WeekSummary,
} from '../types'

const NONE: Nutrients = {
  kcal: null,
  protein_g: null,
  fat_g: null,
  sat_fat_g: null,
  carbs_available_g: null,
  sugars_g: null,
  fibre_g: null,
  sodium_mg: null,
  iron_mg: null,
  calcium_mg: null,
  zinc_mg: null,
  folate_ug: null,
  vit_a_ug: null,
  vit_b12_ug: null,
  vit_c_mg: null,
  vit_d_ug: null,
}

export function nutrients(values: Partial<Nutrients>): Nutrients {
  return { ...NONE, ...values }
}

// nutrition-mcp's numbers for an amount of one food.
export function numbersOf(
  food: { id: string; name: string; units?: Record<string, number> },
  amount: number,
  unit: string,
  grams: number,
  values: Partial<Nutrients>,
): NutritionResult {
  return {
    food: {
      id: food.id,
      kind: 'dish',
      name: food.name,
      aliases: [],
      regions: [],
      diet: 'veg',
      source_ids: ['ifct2017'],
      units: food.units ?? { g: 1 },
    },
    amount,
    unit,
    grams,
    measure_assumption: `${grams} g`,
    nutrients: nutrients(values),
    incomplete: {},
    raw_ingredient_totals: [],
    gi: null,
    gi_ref: null,
    glycemic_load: null,
    oil_assumption: null,
    sources: ['ifct2017'],
  }
}

export function mealOf(items: NutritionResult[], total: Partial<Nutrients>): MealResult {
  return {
    items,
    total: nutrients(total),
    total_incomplete: {},
    glycemic_load: null,
    glycemic_load_missing: items.map((item) => item.food.id),
  }
}

export const CHAPATI = { id: 'chapati', name: 'Chapati', units: { g: 1, piece: 40 } }
export const CURD = { id: 'curd', name: 'Curd', units: { g: 1, katori: 150 } }
export const RICE = { id: 'steamed_rice', name: 'Steamed rice', units: { g: 1, katori: 150 } }
export const PANEER = { id: 'paneer', name: 'Paneer', units: { g: 1 } }

const chapatiNumbers = numbersOf(CHAPATI, 3, 'piece', 120, { kcal: 357, protein_g: 11.2 })

// "3 chapatis, some curd, half plate rice and bhindi sabzi": one food matched, one with no
// amount, one in a unit nobody has, and one the data lacks.
export function readReply(): ReadResult {
  const items: ReadItem[] = [
    {
      name: 'chapatis',
      food_id: 'chapati',
      food_name: 'Chapati',
      amount: 3,
      unit: 'piece',
      grams: 120,
      status: 'matched',
      valid_units: [],
      nutrition: chapatiNumbers,
    },
    {
      name: 'curd',
      food_id: 'curd',
      food_name: 'Curd',
      amount: null,
      unit: null,
      grams: null,
      status: 'needs_amount',
      valid_units: ['g', 'katori'],
      nutrition: null,
    },
    {
      name: 'rice',
      food_id: 'steamed_rice',
      food_name: 'Steamed rice',
      amount: 0.5,
      unit: 'plate',
      grams: null,
      status: 'unknown_unit',
      valid_units: ['g', 'katori'],
      nutrition: null,
    },
    {
      name: 'bhindi sabzi',
      food_id: null,
      food_name: null,
      amount: null,
      unit: null,
      grams: null,
      status: 'missing',
      valid_units: [],
      nutrition: null,
    },
  ]
  return {
    text_length: 53,
    items,
    total: mealOf([chapatiNumbers], { kcal: 357, protein_g: 11.2 }),
    missing: ['bhindi sabzi'],
  }
}

// The compute route's reply for chapati and 100 g of curd, with a total no test adds up to.
export function computeReply(): MealResult {
  return mealOf([chapatiNumbers, numbersOf(CURD, 100, 'g', 100, { kcal: 63, protein_g: 3.1 })], {
    kcal: 420,
    protein_g: 14.3,
  })
}

// One date of a summary: Monday 26 October of the sample log, a training day, missing protein
// and with fibre unsettled. Breakfast and lunch were eaten; lunch has a glycemic load.
export function daySummary(changes: Partial<DaySummary> = {}): DaySummary {
  return {
    date: '2026-10-26',
    day_type: 'training',
    totals: {
      ...nutrients({
        kcal: 2410,
        protein_g: 118,
        fat_g: 70,
        sat_fat_g: 21,
        carbs_available_g: 320,
      }),
      fibre_g: 22,
      calcium_mg: 1040,
      sodium_mg: 1900,
    },
    targets: {
      kcal: 2650,
      protein_g: 150,
      sat_fat_g: 28,
      fibre_g: 35,
      carbs_available_g: 300,
      fat_g: 80,
      calcium_mg: 1000,
      sodium_mg: 2300,
    },
    failures: [{ check: 'day_protein_g', message: 'protein 118 g, the day needs at least 150 g' }],
    unsettled: [
      {
        check: 'day_fibre_g',
        message: 'fibre not known (no value for moong dal); the part known is 22 g',
      },
    ],
    soft: { carbs_available_g: 0.07, fat_g: -0.125 },
    glycemic_load: { breakfast: null, lunch: 18.4, snack: null },
    gl_missing: { breakfast: 2, lunch: 0, snack: 1 },
    ...changes,
  }
}

export function weekSummary(changes: Partial<WeekSummary> = {}): WeekSummary {
  return {
    days: [daySummary()],
    as_planned_share: 0.5,
    days_on_target: 0,
    days_unsettled: 0,
    planned_meals: 4,
    ...changes,
  }
}
