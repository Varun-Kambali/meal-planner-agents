import { describe, expect, it } from 'vitest'

import { addDays, dateWords, daysFrom, isDate, weekdayOf } from './dates'
import { followable, recipeGrams, recipeItems, tooLong, withEntry } from './mealLog'
import { sampleWeek } from './test/helpers'
import type { LogEntry, MealLog } from './types'

function skipped(date: string, slot = 'breakfast'): LogEntry {
  return { date, slot_id: slot, status: 'skipped', items: [] }
}

describe('dates as the log keeps them', () => {
  it('reads the weekday as the summary does, with Monday first', () => {
    expect(weekdayOf('2026-11-02')).toBe('mon')
    expect(weekdayOf('2026-11-08')).toBe('sun')
    expect(weekdayOf('2024-02-29')).toBe('thu')
  })

  it('moves by whole days across months, years and clock changes', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2027-01-01', -1)).toBe('2026-12-31')
    // Clocks change in Europe on 25 October 2026; a day is still a day.
    expect(addDays('2026-10-24', 2)).toBe('2026-10-26')
    expect(daysFrom('2026-10-26', '2026-11-01')).toBe(7)
    expect(daysFrom('2026-11-02', '2026-11-02')).toBe(1)
  })

  it('takes only days of the calendar', () => {
    expect(isDate('2026-11-02')).toBe(true)
    expect(isDate('2026-02-30')).toBe(false)
    expect(isDate('2026-11-2')).toBe(false)
  })

  it('says a date in words, with the year only when it is not this one', () => {
    expect(dateWords('2026-11-02', '2026-11-06')).toBe('Monday 2 November')
    expect(dateWords('2025-12-31', '2026-01-01')).toBe('Wednesday 31 December 2025')
  })
})

describe('the meal log', () => {
  const log: MealLog = { version: 1, entries: [skipped('2026-11-02'), skipped('2026-11-03')] }

  it('replaces the entry of the same date and slot, and adds any other', () => {
    const swapped: LogEntry = {
      date: '2026-11-02',
      slot_id: 'breakfast',
      status: 'swapped',
      items: [{ food_id: 'idli', amount: 2, unit: 'piece' }],
    }

    expect(withEntry(log, swapped).entries).toEqual([swapped, skipped('2026-11-03')])
    expect(withEntry(log, skipped('2026-11-02', 'lunch')).entries).toHaveLength(3)
    expect(log.entries).toHaveLength(2)
  })

  it('holds a log to seven consecutive days', () => {
    expect(tooLong(log, skipped('2026-11-08'))).toBeNull()
    expect(tooLong(log, skipped('2026-11-09'))).toEqual({
      first: '2026-11-02',
      last: '2026-11-09',
      days: 8,
    })
    expect(tooLong(log, skipped('2026-10-27'))).toEqual({
      first: '2026-10-27',
      last: '2026-11-03',
      days: 8,
    })
  })
})

describe('a recipe as it is logged', () => {
  it('logs the ingredients at their final grams, or each scaled by one factor', () => {
    const breakfast = sampleWeek().recipes[0]!

    expect(recipeItems(breakfast)).toEqual([
      { food_id: 'banana', amount: 182.19, unit: 'g' },
      { food_id: 'oats', amount: 99.38, unit: 'g' },
      { food_id: 'curd', amount: 49.69, unit: 'g' },
    ])
    expect(recipeItems(breakfast, 0.5).map((item) => item.amount)).toEqual([91.1, 49.69, 24.85])
    expect(recipeGrams(breakfast)).toBeCloseTo(331.26)
    // A tiny share of a small amount stays above 0, as a log item's amount must be.
    expect(recipeItems(breakfast, 0.00001).map((item) => item.amount)).toEqual([0.01, 0.01, 0.01])
  })

  it('follows any written recipe with numbers, whether or not it met its checks', () => {
    const week = sampleWeek()
    const lunch = week.recipes[1]!
    expect(followable(lunch)).toBe(true)
    lunch.failures = [{ check: 'slot_protein', message: 'protein 31 g, the meal needs 40 g' }]
    expect(followable(lunch)).toBe(true)
    // nutrition-mcp could not work it out, so the summary would refuse its foods.
    lunch.nutrition = null
    expect(followable(lunch)).toBe(false)
    expect(followable(undefined)).toBe(false)
  })
})
