import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { SaveOutcome } from './session'
import type { State } from './storage'
import { FakeStream, fakeApi, sampleState, sampleWeek } from './test/helpers'
import { useWeekRun } from './useWeekRun'

function saver() {
  return vi.fn<(state: State) => Promise<SaveOutcome>>(async () => ({ ok: true }))
}

describe('the end of a run', () => {
  it('keeps no week when the plan changed while it was being written', async () => {
    const run = new FakeStream()
    fakeApi({ '/v1/week': () => run.response })
    const save = saver()
    const before: State = { ...sampleState(), week: null }
    const { result, rerender } = renderHook(({ state }) => useWeekRun(state, save, () => {}), {
      initialProps: { state: before },
    })

    act(() => result.current.write())
    await waitFor(() => expect(result.current.running).not.toBeNull())
    const changed: State = { ...before, rules: { ...before.rules!, name: 'Loaded from a file' } }
    rerender({ state: changed })
    run.send({ kind: 'week_done', week: sampleWeek() })
    run.end()

    await waitFor(() => expect(result.current.running).toBeNull())
    expect(save).not.toHaveBeenCalled()
    expect(result.current.outcome).toMatchObject({
      kind: 'failed',
      message: 'The plan changed while the week was being written, so this week was not kept.',
    })
  })
})
