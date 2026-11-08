// Whether the API's host is up, for a build that talks to one (VITE_API_BASE is set). A free host
// stops a server after 15 minutes without a request and starts it again on the next, which takes
// about a minute. Until it answers, a page that said nothing would look broken.
//
// The first call gets 4 seconds. If it fails, the person is told, and the call is made again (15
// seconds each, 3 seconds apart) until one answers or 120 seconds have passed in all. Then the
// notice says the server did not answer, and the calls go on 30 seconds apart, so the notice
// clears itself when the server comes up.

import { useEffect, useState } from 'react'

import { health } from './api'

export type Wake = 'checking' | 'up' | 'starting' | 'gave_up'

const FIRST_TIMEOUT_MS = 4_000
const RETRY_TIMEOUT_MS = 15_000
const RETRY_GAP_MS = 3_000
const GIVE_UP_MS = 120_000
const SLOW_GAP_MS = 30_000

// True if the server answered within `timeoutMs`, or until `stop` fires.
function answers(timeoutMs: number, stop: AbortSignal): Promise<boolean> {
  const call = new AbortController()
  const end = (): void => call.abort()
  stop.addEventListener('abort', end)
  const timer = setTimeout(end, timeoutMs)
  return health({ signal: call.signal })
    .then(
      () => true,
      () => false,
    )
    .finally(() => {
      clearTimeout(timer)
      stop.removeEventListener('abort', end)
    })
}

function pause(ms: number, stop: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const done = (): void => {
      clearTimeout(timer)
      stop.removeEventListener('abort', done)
      resolve()
    }
    const timer = setTimeout(done, ms)
    stop.addEventListener('abort', done)
  })
}

export function useServerWake(): Wake {
  const [wake, setWake] = useState<Wake>('checking')

  useEffect(() => {
    // Read inside the effect, so that a test can set it; Vite fixes it at build time.
    if (!import.meta.env.VITE_API_BASE) return
    const stop = new AbortController()
    let slow = false
    const giveUp = setTimeout(() => {
      slow = true
      setWake((now) => (now === 'up' ? now : 'gave_up'))
    }, GIVE_UP_MS)

    void (async () => {
      let timeout = FIRST_TIMEOUT_MS
      for (;;) {
        const up = await answers(timeout, stop.signal)
        if (stop.signal.aborted) return
        if (up) {
          clearTimeout(giveUp)
          setWake('up')
          return
        }
        // The same value again changes nothing, so the message is announced once. Once it has
        // said the server did not answer, it is not changed back to "starting".
        setWake((now) => (now === 'gave_up' ? now : 'starting'))
        await pause(slow ? SLOW_GAP_MS : RETRY_GAP_MS, stop.signal)
        if (stop.signal.aborted) return
        timeout = RETRY_TIMEOUT_MS
      }
    })()

    return () => {
      clearTimeout(giveUp)
      stop.abort()
    }
  }, [])

  return wake
}
