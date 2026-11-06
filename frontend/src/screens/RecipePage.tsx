// A recipe to cook from, and every recipe of the week to print, one to an A4 page. Grams and
// household measures come from the recipe, numbers from nutrition-mcp through the API; nothing is
// worked out here. A step that quotes a catalogue id shows the food's name, once the catalogue
// the shell holds has loaded.

import { useEffect, useId, useMemo } from 'react'

import type { DayKey } from '../api'
import type { State } from '../storage'
import type { CheckedRecipe, PlanRules } from '../types'
import { foodNames, type SharedCatalogue } from '../useCatalogue'
import {
  COOK_WHEN,
  DAYS,
  DAY_NAMES,
  GEAR,
  NUTRIENTS,
  NUTRIENT_KEYS,
  measureWords,
  shown,
  stepNamer,
  type NutrientKey,
} from '../words'
import { Status } from './parts'

export interface RecipePageProps {
  state: State
  // Null for every recipe of the week.
  day: DayKey | null
  slotId: string | null
  // The catalogue the shell holds, for the food names in the steps (useCatalogue).
  catalogue: SharedCatalogue
}

function byWeekOrder(rules: PlanRules | null, recipes: CheckedRecipe[]): CheckedRecipe[] {
  const slots = rules?.slots.map((slot) => slot.id) ?? []
  const place = (recipe: CheckedRecipe): number =>
    DAYS.indexOf(recipe.spec.day) * 100 + slots.indexOf(recipe.spec.slot.id)
  return [...recipes].sort((a, b) => place(a) - place(b))
}

export function RecipePage({ state, day, slotId, catalogue }: RecipePageProps) {
  const { rules, week } = state
  const { need } = catalogue
  useEffect(() => need(), [need])
  const catalogueNames = useMemo(() => foodNames(catalogue.state), [catalogue.state])
  const nameFoods = useMemo(() => stepNamer(catalogueNames), [catalogueNames])
  if (day === null || slotId === null) {
    const recipes = byWeekOrder(rules, week?.recipes ?? [])
    return (
      <>
        <p className="screen-only">
          <a href="#/week">Back to the week</a>
        </p>
        <h1 tabIndex={-1} data-screen-title className="screen-only">
          Recipes for the week
        </h1>
        {recipes.length === 0 ? (
          <p>No week has been written yet.</p>
        ) : (
          <>
            <p className="screen-only">
              Each recipe prints on a page of its own.{' '}
              <button type="button" className="button-secondary" onClick={() => window.print()}>
                Print every recipe
              </button>
            </p>
            {recipes.map((recipe) => (
              <Recipe
                key={`${recipe.spec.day}-${recipe.spec.slot.id}`}
                recipe={recipe}
                rules={rules}
                nameFoods={nameFoods}
                level={2}
              />
            ))}
          </>
        )}
      </>
    )
  }
  const recipe = week?.recipes.find((r) => r.spec.day === day && r.spec.slot.id === slotId)
  if (recipe === undefined) {
    const slot = rules?.slots.find((s) => s.id === slotId)
    return (
      <>
        <h1 tabIndex={-1} data-screen-title>
          No recipe here
        </h1>
        <p>
          This week has no recipe for {DAY_NAMES[day]}&apos;s {slot?.name ?? slotId}.
        </p>
        <p>
          <a href="#/week">Back to the week</a>
        </p>
      </>
    )
  }
  return (
    <>
      <p className="screen-only">
        <a href="#/week">Back to the week</a>
      </p>
      <Recipe recipe={recipe} rules={rules} nameFoods={nameFoods} level={1} />
      <p className="screen-only">
        <button type="button" className="button-secondary" onClick={() => window.print()}>
          Print this recipe
        </button>
      </p>
    </>
  )
}

// The numbers a printed page keeps, in one line: the full table stays on screen, so the recipe
// fits on its A4 page.
const PRINTED: NutrientKey[] = [
  'kcal',
  'protein_g',
  'carbs_available_g',
  'fat_g',
  'sat_fat_g',
  'fibre_g',
]

function Recipe({
  recipe,
  rules,
  nameFoods,
  level,
}: {
  recipe: CheckedRecipe
  rules: PlanRules | null
  // A step with the catalogue's names for the ids it quotes; as written until it has loaded.
  nameFoods: (step: string) => string
  level: 1 | 2
}) {
  const { spec, draft, nutrition } = recipe
  const titleId = useId()
  const stepsId = useId()
  const Title = level === 1 ? 'h1' : 'h2'
  const Section = level === 1 ? 'h2' : 'h3'
  const names = new Map(nutrition?.items.map((item) => [item.food.id, item.food.name]) ?? [])
  const nameOf = (id: string): string => names.get(id) ?? id
  const dayType = rules?.day_types.find((d) => d.id === spec.day_type)?.name ?? spec.day_type
  const amount = (key: NutrientKey): string => {
    const value = nutrition?.total[key] ?? null
    if (value === null) return 'not known'
    const lower = (nutrition?.total_incomplete[key] ?? []).length > 0
    return `${lower ? 'at least ' : ''}${shown(value)} ${NUTRIENTS[key].unit}`
  }
  const partial = NUTRIENT_KEYS.filter(
    (key) => nutrition?.total[key] != null && (nutrition.total_incomplete[key] ?? []).length > 0,
  )
  return (
    <article className="recipe" aria-labelledby={titleId}>
      <Title
        id={titleId}
        tabIndex={level === 1 ? -1 : undefined}
        data-screen-title={level === 1 ? true : undefined}
      >
        {draft.title}
      </Title>
      <p className="meta">
        {DAY_NAMES[spec.day]}, {spec.slot.name} at {spec.slot.eat_at.slice(0, 5)} · {draft.cuisine}
      </p>
      <section className="recipe-status" aria-label="Checks">
        {recipe.failures.length === 0 ? (
          <p>
            <Status verdict="ok">every check</Status>
          </p>
        ) : (
          <>
            <p>
              <Status verdict="fail" word="Misses:" />
            </p>
            <ul className="reasons">
              {recipe.failures.map((failure, index) => (
                <li key={index}>{failure.message}</li>
              ))}
            </ul>
          </>
        )}
      </section>
      <dl className="facts">
        <div>
          <dt>Time</dt>
          <dd>
            {draft.active_min} min active, {draft.total_min} min in all
          </dd>
        </div>
        <div>
          <dt>Cook</dt>
          <dd>{COOK_WHEN[spec.slot.cook_when]}</dd>
        </div>
        <div>
          <dt>Gear</dt>
          <dd>{draft.gear.map((gear) => GEAR[gear]).join(', ')}</dd>
        </div>
        <div className="screen-only">
          <dt>Meal target</dt>
          <dd>
            {shown(spec.kcal)} kcal and at least {shown(spec.protein_g)} g protein, its share of a{' '}
            {dayType}
          </dd>
        </div>
      </dl>
      <div className="cook">
        <div>
          <table className="numbers ingredients">
            <caption>Ingredients for one serving</caption>
            <thead>
              <tr>
                <th scope="col">Ingredient</th>
                <th scope="col" className="num">
                  Amount
                </th>
              </tr>
            </thead>
            <tbody>
              {draft.ingredients.map((ingredient, index) => {
                // A file saved before measures were shown has none.
                const measure = recipe.measures[index] ?? null
                return (
                  <tr key={index}>
                    <td>{nameOf(ingredient.id)}</td>
                    <td className="num">
                      {shown(ingredient.grams)} <span className="unit">g</span>
                      {measure !== null && (
                        <span className="measure">{`, ${measureWords(measure)}`}</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {recipe.portion_scale !== 1 && (
            <p className="meta">
              Amounts scaled by{' '}
              {recipe.portion_scale.toLocaleString(undefined, { maximumFractionDigits: 2 })} to fit
              the meal&apos;s calories.
            </p>
          )}
        </div>
        <div>
          <Section id={stepsId}>Steps</Section>
          <ol className="steps" aria-labelledby={stepsId}>
            {draft.steps.map((step, index) => (
              <li key={index}>{nameFoods(step)}</li>
            ))}
          </ol>
        </div>
      </div>
      <Section>Keeping it</Section>
      <p>
        <strong>Store:</strong> {draft.store}
      </p>
      <p>
        <strong>Swap:</strong> {draft.swap}
      </p>
      <p>
        <strong>How it fits:</strong> {draft.fits}
      </p>
      <Section>Numbers</Section>
      {nutrition === null ? (
        <p>nutrition-mcp could not work out this recipe&apos;s numbers, so none are shown.</p>
      ) : (
        <>
          <p className="print-only">
            Per serving:{' '}
            {PRINTED.map((key) => `${amount(key)} ${NUTRIENTS[key].name.toLowerCase()}`).join(', ')}
            .
          </p>
          <table className="numbers nutrients screen-only">
            <caption>Nutrients in one serving</caption>
            <thead>
              <tr>
                <th scope="col">Nutrient</th>
                <th scope="col" className="num">
                  Amount
                </th>
              </tr>
            </thead>
            <tbody>
              {NUTRIENT_KEYS.map((key) => (
                <tr key={key}>
                  <th scope="row">{NUTRIENTS[key].name}</th>
                  <td className="num">
                    <Amount nutrition={nutrition} nutrient={key} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {partial.map((key) => (
            // A printed page keeps the note only for the six numbers it prints.
            <p key={key} className={PRINTED.includes(key) ? 'meta' : 'meta screen-only'}>
              {NUTRIENTS[key].name} is at least this: no value for{' '}
              {(nutrition.total_incomplete[key] ?? []).map(nameOf).join(', ')} in the data.
            </p>
          ))}
          <p>
            Glycemic load:{' '}
            {nutrition.glycemic_load === null
              ? `not known: ${nutrition.glycemic_load_missing.length} of ${nutrition.items.length} ingredients have no value`
              : shown(nutrition.glycemic_load)}
          </p>
        </>
      )}
      {recipe.attempts > 1 && <p className="meta screen-only">Tried {recipe.attempts} times.</p>}
    </article>
  )
}

// A total in the table: the number, then its unit in muted text, with "at least" for a lower
// bound and "not known" when no food has a value.
function Amount({
  nutrition,
  nutrient,
}: {
  nutrition: NonNullable<CheckedRecipe['nutrition']>
  nutrient: NutrientKey
}) {
  const value = nutrition.total[nutrient]
  if (value === null) return <>not known</>
  const lower = (nutrition.total_incomplete[nutrient] ?? []).length > 0
  return (
    <>
      {lower && 'at least '}
      {shown(value)} <span className="unit">{NUTRIENTS[nutrient].unit}</span>
    </>
  )
}
