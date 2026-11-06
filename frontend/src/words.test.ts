import { describe, expect, it } from 'vitest'

import { measureWords, quotedNames, textNamer } from './words'

describe('a household measure in words', () => {
  it('writes quarters and halves as fractions', () => {
    expect(measureWords({ amount: 0.25, unit: 'tbsp' })).toBe('¼ tbsp')
    expect(measureWords({ amount: 0.5, unit: 'piece' })).toBe('½ piece')
    expect(measureWords({ amount: 0.75, unit: 'cup' })).toBe('¾ cup')
    expect(measureWords({ amount: 1.5, unit: 'piece' })).toBe('1½ pieces')
    expect(measureWords({ amount: 2.25, unit: 'cup' })).toBe('2¼ cups')
  })

  it('writes a whole amount as a number, and a unit in the plural above one', () => {
    expect(measureWords({ amount: 1, unit: 'cup' })).toBe('1 cup')
    expect(measureWords({ amount: 15, unit: 'piece' })).toBe('15 pieces')
    expect(measureWords({ amount: 3, unit: 'clove' })).toBe('3 cloves')
    expect(measureWords({ amount: 2, unit: 'leaf' })).toBe('2 leaves')
    expect(measureWords({ amount: 2, unit: 'glass' })).toBe('2 glasses')
  })

  it('keeps an abbreviated unit, and a unit it has no plural for, as the data gives it', () => {
    expect(measureWords({ amount: 3, unit: 'tbsp' })).toBe('3 tbsp')
    expect(measureWords({ amount: 1.25, unit: 'tsp' })).toBe('1¼ tsp')
    expect(measureWords({ amount: 2, unit: 'bunch' })).toBe('2 bunch')
  })

  it('shows an amount that is not a quarter as a number', () => {
    expect(measureWords({ amount: 1.2, unit: 'cup' })).toBe('1.2 cups')
  })
})

describe("names in the writer's text", () => {
  const names = new Map([
    ['brown_rice', 'Brown rice'],
    ['curry_leaves', 'Curry leaves'],
    ['curd', 'Curd (dahi)'],
    ['egg', 'Egg'],
    ['egg_white', 'Egg white'],
    ['greek_yogurt', 'Greek yogurt (low fat)'],
    ['green_beans', 'French beans'],
    ['turmeric', 'Turmeric powder'],
  ])
  const nameFoods = textNamer(names)

  it('puts the catalogue name in place of a catalogue id', () => {
    expect(nameFoods('Cook brown_rice in a pressure_cooker.')).toBe(
      'Cook brown rice in a pressure_cooker.',
    )
    expect(nameFoods('Fry the curry_leaves, then more curry_leaves.')).toBe(
      'Fry the curry leaves, then more curry leaves.',
    )
  })

  it('keeps the name as written at the start of a step', () => {
    expect(nameFoods('brown_rice goes in first.')).toBe('Brown rice goes in first.')
  })

  it('keeps the capital of a name that starts with a proper adjective', () => {
    expect(nameFoods('Top with greek_yogurt and green_beans.')).toBe(
      'Top with Greek yogurt (low fat) and French beans.',
    )
  })

  it('leaves an id of one word as written, since it is already a word of the step', () => {
    expect(nameFoods('Stir the turmeric into the curd and add an egg.')).toBe(
      'Stir the turmeric into the curd and add an egg.',
    )
  })

  it('replaces whole words only', () => {
    expect(nameFoods('Whisk the egg_white with 2 eggs.')).toBe('Whisk the egg white with 2 eggs.')
    expect(nameFoods('Add brown_rice_flour.')).toBe('Add brown_rice_flour.')
  })

  it('names the foods of every step it is given, one after another', () => {
    expect(nameFoods('Rinse the brown_rice.')).toBe('Rinse the brown rice.')
    expect(nameFoods('Rinse the brown_rice.')).toBe('Rinse the brown rice.')
    expect(nameFoods('Soak the brown_rice.')).toBe('Soak the brown rice.')
  })

  it('shows a gear id by the label the gear row uses, in lower case mid-sentence', () => {
    const nameIds = textNamer(quotedNames(names))

    expect(nameIds('Cook brown_rice in a pressure_cooker, then a stovetop_pan.')).toBe(
      'Cook brown rice in a pressure cooker, then a stovetop pan.',
    )
    expect(nameIds('Chill it in a jar_with_lid.')).toBe('Chill it in a jar with a lid.')
    expect(nameIds('rice_cooker on, then wait.')).toBe('Rice cooker on, then wait.')
  })

  it('names gear before the catalogue has loaded, and leaves its ids as written', () => {
    const nameIds = textNamer(quotedNames(new Map()))

    expect(nameIds('Cook moong_dal in a pressure_cooker.')).toBe(
      'Cook moong_dal in a pressure cooker.',
    )
  })

  it('leaves a step as written when there are no names', () => {
    expect(textNamer(new Map())('Cook brown_rice.')).toBe('Cook brown_rice.')
  })
})
