import { describe, expect, it } from 'vitest'

import { buildRules, draftOf, fromServer } from './planForm'
import { sampleState } from './test/helpers'

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
})
