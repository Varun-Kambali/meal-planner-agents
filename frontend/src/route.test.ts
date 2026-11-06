import { describe, expect, it } from 'vitest'

import { parseRoute } from './route'

describe('the screen an address names', () => {
  it('reads each screen', () => {
    expect(parseRoute('')).toEqual({ name: 'home' })
    expect(parseRoute('#/plan')).toEqual({ name: 'plan' })
    expect(parseRoute('#/recipe/mon/evening%20snack')).toEqual({
      name: 'recipe',
      day: 'mon',
      slotId: 'evening snack',
    })
    expect(parseRoute('#/recipe/funday/lunch')).toEqual({ name: 'missing' })
  })

  it('reads the log and the summary, with a date or without one', () => {
    expect(parseRoute('#/log')).toEqual({ name: 'log', date: null })
    expect(parseRoute('#/log/2026-11-02')).toEqual({ name: 'log', date: '2026-11-02' })
    expect(parseRoute('#/summary')).toEqual({ name: 'summary' })
    expect(parseRoute('#/summary/2026-11-02')).toEqual({ name: 'day', date: '2026-11-02' })
    expect(parseRoute('#/log/2026-02-30')).toEqual({ name: 'missing' })
    expect(parseRoute('#/summary/today')).toEqual({ name: 'missing' })
  })

  it('leaves the screen alone for a "#" that names an element', () => {
    expect(parseRoute('#main')).toBeNull()
  })

  it('takes an address it cannot decode for a missing page', () => {
    expect(parseRoute('#/recipe/mon/%')).toEqual({ name: 'missing' })
    expect(parseRoute('#/%E0%A4%A')).toEqual({ name: 'missing' })
  })
})
