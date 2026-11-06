import { describe, expect, it } from 'vitest'

import { measureWords, withFoodNames } from './words'

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

describe('food names in a step', () => {
  const names = new Map([
    ['brown_rice', 'Brown rice'],
    ['curry_leaves', 'Curry leaves'],
    ['egg', 'Egg'],
    ['egg_white', 'Egg white'],
    ['onion', 'Onion'],
  ])

  it('puts the catalogue name in place of a catalogue id', () => {
    expect(withFoodNames('Cook brown_rice in a pressure_cooker.', names)).toBe(
      'Cook brown rice in a pressure_cooker.',
    )
    expect(withFoodNames('Fry the onion and curry_leaves, then more curry_leaves.', names)).toBe(
      'Fry the onion and curry leaves, then more curry leaves.',
    )
  })

  it('keeps the name as written at the start of a step', () => {
    expect(withFoodNames('brown_rice goes in first.', names)).toBe('Brown rice goes in first.')
  })

  it('replaces whole words only', () => {
    expect(withFoodNames('Whisk the egg_white with 2 eggs.', names)).toBe(
      'Whisk the egg white with 2 eggs.',
    )
    expect(withFoodNames('Add onions and brown_rice_flour.', names)).toBe(
      'Add onions and brown_rice_flour.',
    )
  })

  it('leaves a step as written when there are no names', () => {
    expect(withFoodNames('Cook brown_rice.', new Map())).toBe('Cook brown_rice.')
  })
})
