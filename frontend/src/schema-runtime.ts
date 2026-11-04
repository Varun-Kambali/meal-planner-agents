// What the generated validators in src/generated/validators.js call while they run: the two
// string formats the schemas use, read as Pydantic reads them, and a string length counted as
// Python counts it. scripts/generate-validators.ts builds the validators with the same formats.

// A date on the calendar: 2026-02-30 has the right shape and is still refused.
function isDate(text: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (match === null) return false
  const [year, month, day] = match.slice(1).map(Number) as [number, number, number]
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  )
}

// A time with or without seconds (a plan says "08:00"), with an optional offset.
const TIME = /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d{1,6})?)?(Z|[+-]([01]\d|2[0-3]):?[0-5]\d)?$/

export const formats = {
  date: { type: 'string', validate: isDate },
  time: { type: 'string', validate: (text: string) => TIME.test(text) },
} as const

// A string's length in characters, as Python's len() counts them, so that a name of 60
// characters with an emoji in it is not refused as 61. (A JavaScript string's own length counts
// an emoji as two.)
export function ucs2length(text: string): number {
  return Array.from(text).length
}
