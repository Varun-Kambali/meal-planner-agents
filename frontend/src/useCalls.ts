// The calls a screen has open, so that leaving it stops them. A call is aborted if the screen
// goes while it runs, and forgotten once it is done.

import { useCallback, useEffect, useRef } from 'react'

export function isAbort(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === 'AbortError'
}

// Runs `start` with a signal the screen aborts when it goes, and resolves or rejects as it does.
export type Call = <T>(start: (signal: AbortSignal) => Promise<T>) => Promise<T>

export function useCalls(): Call {
  const open = useRef(new Set<AbortController>())
  useEffect(() => {
    const calls = open.current
    return () => {
      for (const call of calls) call.abort()
    }
  }, [])
  return useCallback(async <T>(start: (signal: AbortSignal) => Promise<T>): Promise<T> => {
    const controller = new AbortController()
    open.current.add(controller)
    try {
      return await start(controller.signal)
    } finally {
      open.current.delete(controller)
    }
  }, [])
}
