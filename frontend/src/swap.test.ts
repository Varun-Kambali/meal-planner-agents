import { describe, expect, it } from 'vitest'

import { computed, counted, logItems, stillToDo, swapItems, withChange } from './swap'
import { CURD, computeReply, readReply } from './test/tracking'

describe('a swapped meal being confirmed', () => {
  it('starts from the reader, with a matched food unconfirmed and its units known', () => {
    const [chapati, curd, rice, sabzi] = swapItems(readReply())

    expect(chapati).toMatchObject({ status: 'matched', confirmed: false, units: ['piece', 'g'] })
    expect(curd).toMatchObject({ status: 'needs_amount', amount: null, units: ['g', 'katori'] })
    expect(rice).toMatchObject({ status: 'unknown_unit', amount: 0.5, unit: 'plate' })
    expect(sabzi).toMatchObject({ status: 'missing', food: null })
  })

  it('says what is left before saving, and never asks anything of a missing food', () => {
    const items = swapItems(readReply())

    expect(stillToDo(items, new Set())).toEqual([
      'confirm Chapati',
      'give an amount for Curd',
      'choose a unit for Steamed rice',
    ])
    const confirmed = items.map((item) => (item.key === 0 ? { ...item, confirmed: true } : item))
    expect(stillToDo(confirmed, new Set([0]))[0]).toBe('finish changing Chapati')
  })

  it('takes the compute route numbers for what was sent, and saves only counted foods', () => {
    const items = swapItems(readReply())
    const change = { food: CURD, amount: 100, unit: 'g', units: ['g', 'katori'] }
    const next = withChange(items, 1, change)
    const sent = counted(next)
    expect(sent.map((item) => item.food?.id)).toEqual(['chapati', 'curd'])

    const done = computed(next, sent, computeReply().items, 1)

    expect(done[1]).toMatchObject({ status: 'matched', confirmed: true, amount: 100, unit: 'g' })
    expect(done[1]!.numbers?.nutrients.kcal).toBe(63)
    expect(done[0]!.confirmed).toBe(false)
    expect(done[2]!.status).toBe('unknown_unit')
    expect(logItems(done)).toEqual([
      { food_id: 'chapati', amount: 3, unit: 'piece' },
      { food_id: 'curd', amount: 100, unit: 'g' },
    ])
  })
})
