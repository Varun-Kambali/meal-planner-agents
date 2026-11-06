// The calls a screen has open, so that leaving it stops them.

import { useEffect, useRef } from 'react'

export function isAbort(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === 'AbortError'
}

export function useCalls(): () => AbortSignal {
  const open = useRef(new Set<AbortController>())
  useEffect(() => {
    const calls = open.current
    return () => {
      for (const call of calls) call.abort()
    }
  }, [])
  return () => {
    const controller = new AbortController()
    open.current.add(controller)
    return controller.signal
  }
}
