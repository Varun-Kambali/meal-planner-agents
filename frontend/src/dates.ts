// Dates as the log keeps them, YYYY-MM-DD, with no time and no time zone. They are worked with as
// UTC midnights, so moving a day never crosses a change of clocks.

import type { DayKey } from './api'
import { DAYS, DAY_NAMES } from './words'

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

const DAY_MS = 86_400_000

function utc(date: string): Date {
  const [, year, month, day] = ISO_DATE.exec(date)!
  return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)))
}

// A day of the calendar, as the server reads one (2026-02-30 is not).
export function isDate(text: string): boolean {
  if (!ISO_DATE.test(text)) return false
  return utc(text).toISOString().slice(0, 10) === text
}

// The date a clock shows in the reader's own time zone.
export function localDate(now: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export function addDays(date: string, days: number): string {
  return new Date(utc(date).getTime() + days * DAY_MS).toISOString().slice(0, 10)
}

// The days from `first` to `last`, both counted: 1 for the same date.
export function daysFrom(first: string, last: string): number {
  return Math.round((utc(last).getTime() - utc(first).getTime()) / DAY_MS) + 1
}

// The plan's day of the week for a date, as the summary reads it.
export function weekdayOf(date: string): DayKey {
  return DAYS[(utc(date).getUTCDay() + 6) % 7]!
}

// "Monday 2 November", with the year when it is not the year of `today`, a date as YYYY-MM-DD.
export function dateWords(date: string, today?: string): string {
  const day = utc(date)
  const words = `${DAY_NAMES[weekdayOf(date)]} ${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]}`
  return today === undefined || today.slice(0, 4) === date.slice(0, 4)
    ? words
    : `${words} ${date.slice(0, 4)}`
}
