import { expect, it } from 'vitest'

import { checkStored } from '../validate'
import text from './recorded-week.json?raw'

// The accessibility check opens the screens on this state (scripts/a11y-server.ts). When the
// format changes, run scripts/record_week_fixture.py again.
it('keeps the recorded week in the format the app saves', () => {
  const state = checkStored(JSON.parse(text))

  expect(state.week?.recipes).toHaveLength(28)
})
