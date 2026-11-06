import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { StrictMode, useEffect } from 'react'
import { describe, expect, it } from 'vitest'

import { CATALOGUE, fakeApi, json } from './test/helpers'
import { useCatalogue, type FoodCatalogue } from './useCatalogue'

// A screen that needs the catalogue as it opens, as the plan form and the recipe page do.
function Screen({ food }: { food: FoodCatalogue }) {
  const { need } = food
  useEffect(() => need(), [need])
  return <p>{food.list.status}</p>
}

function Shell() {
  return <Screen food={useCatalogue()} />
}

describe('the shared catalogue', () => {
  it('asks for nothing until a screen needs it', () => {
    const calls = fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })

    const { result } = renderHook(() => useCatalogue())

    expect(result.current.list).toEqual({ status: 'idle' })
    expect(calls).toHaveLength(0)
  })

  it('asks once, however many screens need it', async () => {
    const calls = fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const { result } = renderHook(() => useCatalogue())

    act(() => {
      result.current.need()
      result.current.need()
    })
    await waitFor(() => expect(result.current.list).toEqual({ status: 'ready', rows: CATALOGUE }))
    act(() => result.current.need())

    expect(calls.map((call) => call.path)).toEqual(['/v1/catalogue'])
  })

  it('asks again after a failure when a screen needs it next', async () => {
    let answers = 0
    const calls = fakeApi({
      '/v1/catalogue': () => {
        answers += 1
        if (answers === 1) throw new TypeError('Failed to fetch')
        return json(CATALOGUE)
      },
    })
    const { result } = renderHook(() => useCatalogue())

    act(() => result.current.need())
    await waitFor(() => expect(result.current.list.status).toBe('failed'))
    act(() => result.current.need())

    await waitFor(() => expect(result.current.list).toEqual({ status: 'ready', rows: CATALOGUE }))
    expect(calls).toHaveLength(2)
  })

  it('asks again on reload, even once it has loaded', async () => {
    const calls = fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })
    const { result } = renderHook(() => useCatalogue())

    act(() => result.current.need())
    await waitFor(() => expect(result.current.list.status).toBe('ready'))
    act(() => result.current.reload())

    await waitFor(() => expect(calls).toHaveLength(2))
    await waitFor(() => expect(result.current.list.status).toBe('ready'))
  })

  it('stops its call when the page goes', () => {
    const calls = fakeApi({ '/v1/catalogue': () => new Promise<Response>(() => undefined) })
    const { result, unmount } = renderHook(() => useCatalogue())

    act(() => result.current.need())
    unmount()

    expect(calls[0]!.signal?.aborted).toBe(true)
  })

  it('still loads when React mounts the page twice, as it does in development', async () => {
    fakeApi({ '/v1/catalogue': () => json(CATALOGUE) })

    render(
      <StrictMode>
        <Shell />
      </StrictMode>,
    )

    expect(await screen.findByText('ready')).toBeInTheDocument()
  })
})
