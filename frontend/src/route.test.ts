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

  it('leaves the screen alone for a "#" that names an element', () => {
    expect(parseRoute('#main')).toBeNull()
  })

  it('takes an address it cannot decode for a missing page', () => {
    expect(parseRoute('#/recipe/mon/%')).toEqual({ name: 'missing' })
    expect(parseRoute('#/%E0%A4%A')).toEqual({ name: 'missing' })
  })
})
