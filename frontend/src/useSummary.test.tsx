import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { fakeApi, json, sampleState } from './test/helpers'
import { weekSummary } from './test/tracking'
import type { MealLog, PlanRules } from './types'
import { useSummary } from './useSummary'

interface Props {
  rules: PlanRules | null
  log: MealLog
}

function start(): { rules: PlanRules; log: MealLog } {
  const { rules, log } = sampleState()
  return { rules: rules!, log }
}

describe('the summary the shell holds', () => {
  it('asks once for a plan and log, and again only once the log changes', async () => {
    const calls = fakeApi({ '/v1/summary': () => json(weekSummary()) })
    const { result, rerender } = renderHook((props: Props) => useSummary(props.rules, props.log), {
      initialProps: start(),
    })

    act(() => result.current.need())
    act(() => result.current.need())
    await waitFor(() => expect(result.current.state.status).toBe('ready'))
    act(() => result.current.need())
    expect(calls).toHaveLength(1)
    expect(calls[0]!.body).toEqual(start())

    const { rules, log } = start()
    const next = { ...log, entries: log.entries.slice(0, 1) }
    rerender({ rules, log: next })
    expect(result.current.state.status).toBe('idle')
    act(() => result.current.need())

    await waitFor(() => expect(result.current.state.status).toBe('ready'))
    expect(calls).toHaveLength(2)
    expect(calls[1]!.body).toEqual({ rules, log: next })
  })

  it('keeps a failure until asked again', async () => {
    let asked = 0
    fakeApi({
      '/v1/summary': () => {
        asked += 1
        return asked === 1
          ? json({ detail: 'too many requests, try again later' }, 429, { 'Retry-After': '5' })
          : json(weekSummary())
      },
    })
    const props = start()
    const { result: held } = renderHook(() => useSummary(props.rules, props.log))

    act(() => held.current.need())
    await waitFor(() => expect(held.current.state.status).toBe('failed'))
    act(() => held.current.retry())

    await waitFor(() => expect(held.current.state.status).toBe('ready'))
    expect(asked).toBe(2)
  })

  it('asks for nothing while nothing is logged', () => {
    const calls = fakeApi({})
    const { rules } = start()
    const { result } = renderHook(() => useSummary(rules, { version: 1, entries: [] }))

    act(() => result.current.need())

    expect(calls).toHaveLength(0)
    expect(result.current.state.status).toBe('idle')
  })
})
