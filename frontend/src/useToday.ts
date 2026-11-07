// The reader's own date as YYYY-MM-DD, which the log opens on. It moves on at midnight, so a page
// left open overnight logs the new day.

import { useEffect, useState } from 'react'

import { localDate } from './dates'

export function useToday(): string {
  const [today, setToday] = useState(() => localDate(new Date()))
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const untilMidnight = (): void => {
      const now = new Date()
      const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
      timer = setTimeout(() => {
        setToday(localDate(new Date()))
        untilMidnight()
      }, midnight.getTime() - now.getTime())
    }
    untilMidnight()
    return () => clearTimeout(timer)
  }, [])
  return today
}
