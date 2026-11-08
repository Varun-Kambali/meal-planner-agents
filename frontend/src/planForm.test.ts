import { describe, expect, it } from 'vitest'

import { buildRules, draftOf, fromServer, labelOf } from './planForm'
import { sampleState } from './test/helpers'
import type { MealLog, PlanRules } from './types'

describe("the server's problems with the rules", () => {
  it('places a check on the rules as a whole at the field it names', () => {
    const problems = fromServer(
      [
        {
          loc: ['body'],
          msg: "Value error, week: day type not defined in day_types (mon: 'gym'); day_types has: rest",
        },
        { loc: ['body'], msg: 'Value error, slots: ids must be unique, repeated: lunch' },
      ],
      ['body'],
    )

    expect(problems).toEqual([
      {
        path: 'week',
        message: "day type not defined in day_types (mon: 'gym'); day_types has: rest",
      },
      { path: 'slots', message: 'ids must be unique, repeated: lunch' },
    ])
  })

  it('drops the field name an extra target check repeats', () => {
    const [problem] = fromServer(
      [
        {
          loc: ['body', 'day_types', 1, 'extra'],
          msg: "Value error, extra: 'kcal' repeats a target the day type already has; set it in kcal and take it out of extra",
        },
      ],
      ['body'],
    )

    expect(problem).toEqual({
      path: 'day_types.1.extra',
      message:
        "'kcal' repeats a target the day type already has; set it in kcal and take it out of extra",
    })
  })

  it('names the line of a cuisine', () => {
    const [problem] = fromServer(
      [{ loc: ['body', 'food', 'cuisines', 2], msg: 'String should have at least 1 character' }],
      ['body'],
    )

    expect(problem).toEqual({
      path: 'food.cuisines',
      message: 'Line 3: String should have at least 1 character',
    })
  })

  it('reads the rules of a replacement under body.rules and leaves the rest out', () => {
    const problems = fromServer(
      [
        { loc: ['body', 'rules', 'slots', 0, 'eat_at'], msg: 'Input should be a valid time' },
        { loc: ['body', 'slot_id'], msg: "the plan has no slot 'brunch'" },
      ],
      ['body', 'rules'],
    )

    expect(problems).toEqual([{ path: 'slots.0.eat_at', message: 'Input should be a valid time' }])
  })
})

describe('building the rules', () => {
  it('gives an unchanged plan back as it was saved', () => {
    const { rules, log } = sampleState()

    expect(buildRules(draftOf(rules), log)).toEqual({ rules, errors: [] })
  })

  it('keeps shares the form shows rounded as they were saved, such as thirds', () => {
    const { rules } = sampleState()
    const log: MealLog = { version: 1, entries: [] }
    const thirds: [number[], string[]][] = [
      [
        [0.3333, 0.3333, 0.3334],
        ['33.33', '33.33', '33.34'],
      ],
      [
        [1 / 3, 1 / 3, 1 / 3],
        ['33.33', '33.33', '33.33'],
      ],
    ]
    for (const [shares, shown] of thirds) {
      const plan: PlanRules = {
        ...rules!,
        slots: rules!.slots
          .filter((slot) => slot.id !== 'snack')
          .map((slot, i) => ({ ...slot, kcal_share: shares[i]!, protein_share: shares[i]! })),
      }
      const draft = draftOf(plan)

      expect(draft.slots.map((slot) => slot.kcal_share)).toEqual(shown)
      expect(buildRules(draft, log)).toEqual({ rules: plan, errors: [] })
    }
  })

  it('reads a share again once it is edited', () => {
    const { rules, log } = sampleState()
    const draft = draftOf(rules)
    draft.slots[0]!.kcal_share = '20'
    draft.slots[1]!.kcal_share = '35'

    const built = buildRules(draft, log)

    expect(built.rules?.slots.map((slot) => slot.kcal_share)).toEqual([0.2, 0.35, 0.15, 0.3])
  })

  it('names each field that is not a number yet', () => {
    const { rules, log } = sampleState()
    const draft = draftOf(rules)
    draft.dayTypes[0]!.kcal = ''
    draft.slots[1]!.max_active_min = '20.5'

    const built = buildRules(draft, log)

    expect(built.errors).toEqual([
      { path: 'day_types.0.kcal', message: 'Enter a number.' },
      { path: 'slots.1.max_active_min', message: 'Enter a whole number of minutes.' },
    ])
  })

  it('refuses a plan whose meals no longer cover the meal log', () => {
    const { rules, log } = sampleState()
    const draft = draftOf(rules)
    draft.slots = draft.slots.filter((slot) => slot.id !== 'lunch')
    draft.slots[0]!.kcal_share = '55'
    draft.slots[0]!.protein_share = '55'

    const built = buildRules(draft, log)

    expect(built.rules).toBeNull()
    expect(built.errors[0]!.path).toBe('slots')
    expect(built.errors[0]!.message).toMatch(
      /^Your meal log does not fit this plan: .*no slot 'lunch'/,
    )
  })

  it('offers to remove the log entries for a dropped meal when that is the only problem', () => {
    const { rules, log } = sampleState()
    const draft = draftOf(rules)
    draft.slots = draft.slots.filter((slot) => slot.id !== 'lunch')
    draft.slots[0]!.kcal_share = '55'
    draft.slots[0]!.protein_share = '55'

    const built = buildRules(draft, log)

    expect(built.rules).toBeNull()
    expect(built.rules === null && built.removal).toMatchObject({ slotIds: ['lunch'], entries: 1 })
  })

  it('offers no removal when the plan has another problem too', () => {
    const { rules, log } = sampleState()
    const draft = draftOf(rules)
    draft.slots = draft.slots.filter((slot) => slot.id !== 'lunch')

    const built = buildRules(draft, log)

    expect(built.rules === null && built.removal).toBeUndefined()
  })
})

describe('what the form checks before the rules are saved', () => {
  function problemsOf(change: (draft: ReturnType<typeof draftOf>) => void): Record<string, string> {
    const { rules, log } = sampleState()
    const draft = draftOf(rules)
    change(draft)
    const built = buildRules(draft, log)
    expect(built.rules).toBeNull()
    return Object.fromEntries(built.errors.map((error) => [error.path, error.message]))
  }

  it('asks for a name wherever one is blank', () => {
    const found = problemsOf((draft) => {
      draft.name = ' '
      draft.dayTypes[0]!.name = ''
      draft.slots[0]!.name = '  '
    })

    expect(found).toMatchObject({
      name: 'Give the plan a name.',
      'day_types.0.name': 'Give the day type a name.',
      'slots.0.name': 'Give the meal a name.',
    })
  })

  it('says what is wrong with a number that is empty, not a number, or not whole', () => {
    const found = problemsOf((draft) => {
      draft.dayTypes[0]!.kcal = ''
      draft.dayTypes[0]!.protein_g = 'lots'
      draft.slots[0]!.max_active_min = ''
      draft.slots[0]!.max_total_min = '12.5'
    })

    expect(found).toMatchObject({
      'day_types.0.kcal': 'Enter a number.',
      'day_types.0.protein_g': 'Enter a number, such as 2650.',
      'slots.0.max_active_min': 'Enter a number of minutes.',
      'slots.0.max_total_min': 'Enter a whole number of minutes.',
    })
  })

  it('checks an extra target, a meal share, the time and the kind of dish', () => {
    const found = problemsOf((draft) => {
      draft.dayTypes[0]!.extra = [{ key: 'x', nutrient: '', kind: 'min', value: '' }]
      draft.slots[0]!.kcal_share = '0'
      draft.slots[0]!.protein_share = '120'
      draft.slots[1]!.eat_at = ''
      draft.slots[1]!.style = ' '
    })

    expect(found).toMatchObject({
      'day_types.0.extra.0.nutrient': 'Choose a nutrient.',
      'day_types.0.extra.0.value': 'Enter a number.',
      'slots.0.kcal_share': 'Enter a percentage above 0% up to 100%.',
      'slots.0.protein_share': 'Enter a percentage from 0% up to 100%.',
      'slots.1.eat_at': 'Enter the time this meal is eaten.',
      'slots.1.style': 'Say what kind of dish it is.',
    })
  })

  it('asks for gear, a diet, a cuisine and a shopping day, and for at most ten cuisines', () => {
    const none = problemsOf((draft) => {
      draft.gear = []
      draft.diet = ''
      draft.cuisines = ''
      draft.shoppingDays = []
    })
    const many = problemsOf((draft) => {
      draft.cuisines = Array.from({ length: 11 }, (_, i) => `Cuisine ${i}`).join('\n')
    })

    expect(none).toMatchObject({
      gear: 'Choose at least one thing you cook with.',
      'food.diet': 'Choose a diet.',
      'food.cuisines': 'Name at least one cuisine.',
      shopping_days: 'Choose at least one day.',
    })
    expect(many['food.cuisines']).toBe('Name at most 10 cuisines; there are 11.')
  })
})

describe('what a problem is called in the list at the top of the form', () => {
  it('names the field, the meal, the day type and the day', () => {
    expect(labelOf('name')).toBe('Plan name')
    expect(labelOf('week.tue')).toBe('The week, Tuesday')
    expect(labelOf('day_types.1')).toBe('Day type 2')
    expect(labelOf('day_types.1.name')).toBe('Day type 2, name')
    expect(labelOf('day_types.0.kcal')).toMatch(/^Day type 1, /)
    expect(labelOf('day_types.0.extra')).toBe('Day type 1, other daily targets')
    expect(labelOf('day_types.0.extra.1')).toBe('Day type 1, other target 2')
    expect(labelOf('slots.2')).toBe('Meal 3')
    expect(labelOf('slots.2.style')).toMatch(/^Meal 3, /)
    expect(labelOf('nonsense')).toBe('')
  })
})
