import { act, render, screen } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import { ServerWaking } from './ServerWaking'

// What the fake host does with each call to /healthz, in order; the last one repeats.
type Step = 'ok' | 'network' | 'html' | 'hang'

// A fetch that answers /healthz as the steps say. A hanging call ends when it is aborted.
function host(...steps: Step[]): { calls: number; up: boolean } {
  // `up` makes every call from now on answer, whatever the steps say.
  const seen = { calls: 0, up: false }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init: RequestInit = {}) => {
      const step = seen.up ? 'ok' : steps[Math.min(seen.calls, steps.length - 1)]!
      seen.calls += 1
      if (step === 'network') throw new TypeError('Failed to fetch')
      if (step === 'html') return new Response('<html>starting</html>', { status: 200 })
      if (step === 'hang') {
        return new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          )
        })
      }
      return new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return seen
}

async function pass(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

const WAKING = /demo server is starting/i
const GAVE_UP = /did not answer/i

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubEnv('VITE_API_BASE', 'https://api.example.org')
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the waking-server message', () => {
  it('shows nothing when the server answers at once', async () => {
    const seen = host('ok')

    render(<ServerWaking />)
    await pass(200)

    expect(seen.calls).toBe(1)
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    await pass(60_000)
    expect(seen.calls).toBe(1)
  })

  it('says the server is starting when the first call times out after 4 seconds', async () => {
    host('hang', 'ok')

    render(<ServerWaking />)
    await pass(3_900)
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    await pass(200)

    const message = screen.getByRole('status')
    expect(message).toHaveTextContent(WAKING)
    expect(message).toHaveTextContent('15 minutes')
    expect(message).toHaveTextContent('about a minute')
  })

  it('says it at once when the first call fails with a network error', async () => {
    host('network', 'hang')

    render(<ServerWaking />)
    await pass(10)

    expect(screen.getByRole('status')).toHaveTextContent(WAKING)
  })

  it('treats a page that is not JSON, as the host shows while it starts, as not answering', async () => {
    host('html', 'ok')

    render(<ServerWaking />)
    await pass(10)

    expect(screen.getByRole('status')).toHaveTextContent(WAKING)
  })

  it('tries again 3 seconds after each failure, and clears the message when one answers', async () => {
    const seen = host('network', 'hang', 'ok')

    render(<ServerWaking />)
    await pass(10)
    expect(seen.calls).toBe(1)
    await pass(3_000)
    expect(seen.calls).toBe(2)
    // The second call hangs; it is given up after 15 seconds, and the next starts 3 s later.
    await pass(14_900)
    expect(seen.calls).toBe(2)
    expect(screen.getByRole('status')).toHaveTextContent(WAKING)
    await pass(3_200)
    expect(seen.calls).toBe(3)

    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    await pass(60_000)
    expect(seen.calls).toBe(3)
  })

  it('keeps one message through the retries, without replacing it each time', async () => {
    host('network')
    render(<ServerWaking />)
    await pass(10)
    const first = screen.getByText(/demo server is starting/i)

    await pass(9_000)

    expect(screen.getByText(/demo server is starting/i)).toBe(first)
  })

  it('says after 120 seconds that the server did not answer, and keeps asking every 30 seconds', async () => {
    const seen = host('network')

    render(<ServerWaking />)
    await pass(119_000)
    expect(screen.getByRole('status')).toHaveTextContent(WAKING)
    await pass(1_500)

    expect(screen.getByRole('status')).toHaveTextContent(GAVE_UP)
    expect(screen.getByRole('status')).toHaveTextContent('few minutes')
    // Three seconds apart would be 30 calls in 90 seconds; 30 seconds apart is three.
    const calls = seen.calls
    await pass(90_000)
    expect(seen.calls - calls).toBeGreaterThanOrEqual(2)
    expect(seen.calls - calls).toBeLessThanOrEqual(4)
    expect(screen.getByRole('status')).toHaveTextContent(GAVE_UP)
  })

  it('clears the message by itself when the server answers after it gave up', async () => {
    const seen = host('network')

    render(<ServerWaking />)
    await pass(125_000)
    expect(screen.getByRole('status')).toHaveTextContent(GAVE_UP)
    seen.up = true
    await pass(31_000)

    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    const calls = seen.calls
    await pass(120_000)
    expect(seen.calls).toBe(calls)
  })

  it('gives up on a call that is still hanging at 120 seconds', async () => {
    host('hang')

    render(<ServerWaking />)
    await pass(121_000)

    expect(screen.getByRole('status')).toHaveTextContent(GAVE_UP)
  })

  it('does nothing when no API address was set at build time', async () => {
    vi.stubEnv('VITE_API_BASE', '')
    const seen = host('network')

    render(<ServerWaking />)
    await pass(130_000)

    expect(seen.calls).toBe(0)
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })

  it('stops asking when the page goes', async () => {
    const seen = host('network')
    const { unmount } = render(<ServerWaking />)
    await pass(10)
    expect(seen.calls).toBe(1)

    unmount()
    await pass(130_000)

    expect(seen.calls).toBe(1)
  })

  it('stops a call in flight when the page goes', async () => {
    let signal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit = {}) => {
        signal = init.signal ?? undefined
        return new Promise<Response>(() => undefined)
      }),
    )
    const { unmount } = render(<ServerWaking />)
    await pass(10)

    unmount()

    expect(signal?.aborted).toBe(true)
  })

  it('asks once when React mounts the page twice, as it does in development', async () => {
    const seen = host('ok')

    render(
      <StrictMode>
        <ServerWaking />
      </StrictMode>,
    )
    await pass(200)

    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    // The first mount's call is stopped and asked again, so at most two calls in all.
    expect(seen.calls).toBeLessThanOrEqual(2)
  })

  it('asks /healthz of the API named at build time, and is part of the shell', async () => {
    const urls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url)
        throw new TypeError('Failed to fetch')
      }),
    )

    render(<App />)
    await pass(10)

    expect(urls[0]).toBe('https://api.example.org/healthz')
    expect(screen.getByText(WAKING)).toBeInTheDocument()
  })
})
