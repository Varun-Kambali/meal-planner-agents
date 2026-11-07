import { renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'

import { useCalls } from './useCalls'

it('aborts the calls still open when the screen goes, and forgets the ones that are done', async () => {
  const { result, unmount } = renderHook(() => useCalls())
  let finished: AbortSignal | undefined
  let open: AbortSignal | undefined

  await result.current(async (signal) => {
    finished = signal
  })
  void result.current((signal) => {
    open = signal
    return new Promise<never>(() => undefined)
  })
  unmount()

  expect(open?.aborted).toBe(true)
  expect(finished?.aborted).toBe(false)
})
