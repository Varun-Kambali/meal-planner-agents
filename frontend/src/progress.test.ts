import { describe, expect, it } from 'vitest'

import { advance, startProgress, stepWords, type Progress } from './progress'
import type { SlotFailed, WeekResult } from './types'
import { mergeUnwritten } from './useWeekRun'

function stepOf(progress: Progress, slotId: string): string {
  return stepWords(progress.meals.find((meal) => meal.slotId === slotId)!.step)
}

describe('the progress of a run', () => {
  const start = startProgress([
    { id: 'breakfast', name: 'Breakfast' },
    { id: 'lunch', name: 'Lunch' },
  ])

  it('counts checked recipes that miss a check, by day', () => {
    let progress = advance(start, {
      kind: 'slot_written',
      slot_id: 'lunch',
      days: ['mon', 'tue'],
      attempt: 1,
    })
    progress = advance(progress, {
      kind: 'recipe_checked',
      slot_id: 'lunch',
      day: 'mon',
      attempt: 1,
      failures: [],
    })
    progress = advance(progress, {
      kind: 'recipe_checked',
      slot_id: 'lunch',
      day: 'tue',
      attempt: 1,
      failures: [{ check: 'slot_kcal', message: 'too much' }],
    })

    expect(stepOf(progress, 'lunch')).toBe('Checked: 1 of 2 recipes miss a check')
    expect(stepOf(progress, 'breakfast')).toBe('Waiting to start')
    expect(progress.meals.filter((meal) => meal.written)).toHaveLength(1)
  })

  it('says which try a repair or a new writing is, out of four', () => {
    const repairing = advance(start, {
      kind: 'repair_started',
      slot_id: 'lunch',
      days: ['mon'],
      attempt: 4,
    })
    const again = advance(start, {
      kind: 'slot_started',
      slot_id: 'lunch',
      days: ['mon'],
      attempt: 2,
    })

    expect(stepOf(repairing, 'lunch')).toBe('Repairing, try 4 of 4')
    expect(stepOf(again, 'lunch')).toBe('Writing again, try 2 of 4')
  })

  it('tells a reply that will be tried again from a meal that could not be written', () => {
    const event: Omit<SlotFailed, 'final'> = {
      kind: 'slot_failed',
      slot_id: 'lunch',
      days: ['mon'],
      attempt: 1,
      reason: 'timeout',
    }
    const retrying = advance(start, { ...event, final: false })
    const failed = advance(start, { ...event, final: true })

    expect(stepOf(retrying, 'lunch')).toBe(
      'The reply could not be used (the model did not answer in time); trying again',
    )
    expect(stepOf(failed, 'lunch')).toBe('Could not write: the model did not answer in time')
    expect(failed.meals[1]!.written).toBe(true)
  })
})

describe('the meals a replacement could not write', () => {
  it('keeps the earlier ones and takes the replaced meal from the new week', () => {
    const before: WeekResult['unwritten'] = [
      ['mon', 'lunch', 'timeout'],
      ['tue', 'dinner', 'rate_limited'],
    ]

    expect(mergeUnwritten(before, [], 'mon', 'lunch')).toEqual([['tue', 'dinner', 'rate_limited']])
    expect(mergeUnwritten(before, [['mon', 'lunch', 'model_error']], 'mon', 'lunch')).toEqual([
      ['tue', 'dinner', 'rate_limited'],
      ['mon', 'lunch', 'model_error'],
    ])
  })
})
