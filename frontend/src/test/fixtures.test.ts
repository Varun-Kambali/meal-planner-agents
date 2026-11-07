import { expect, it } from 'vitest'

import { checkStored } from '../validate'
import tracking from './recorded-tracking.json?raw'
import text from './recorded-week.json?raw'

// The accessibility check opens the screens on these states (scripts/a11y-server.ts). When the
// format changes, run scripts/record_week_fixture.py, then scripts/record_tracking_fixture.py.
it('keeps the recorded week in the format the app saves', () => {
  const state = checkStored(JSON.parse(text))

  expect(state.week?.recipes).toHaveLength(28)
})

it('keeps the recorded log one the app saves with the recorded week', () => {
  const { log, read } = JSON.parse(tracking) as { log: unknown; read: { reply: unknown } }
  const state = checkStored({ ...(JSON.parse(text) as object), log })

  expect(state.log.entries).toHaveLength(7)
  expect(read.reply).toMatchObject({ missing: ['bhindi sabzi'] })
})
