import { describe, expect, it } from 'vitest'
import { compareSoFar, hasNext, monthGrid, periodLabel, shift } from './history'

describe('history helpers', () => {
  it('shifts by day, month (clamped) and year', () => {
    expect(shift('2026-10-16', 'day', -1)).toBe('2026-10-15')
    expect(shift('2026-01-31', 'month', 1)).toBe('2026-02-28')
    expect(shift('2026-03-01', 'day', -1)).toBe('2026-02-28')
    expect(shift('2024-02-29', 'year', 1)).toBe('2025-03-01')
  })
  it('allows next only when the next period has started', () => {
    expect(hasNext('2026-09-20', 'month', '2026-10-16')).toBe(true)    // October has started
    expect(hasNext('2026-10-01', 'month', '2026-10-16')).toBe(false)
    expect(hasNext('2026-10-15', 'day', '2026-10-16')).toBe(true)
    expect(hasNext('2026-10-16', 'day', '2026-10-16')).toBe(false)
    expect(hasNext('2025-03-03', 'year', '2026-10-16')).toBe(true)
  })
  it('lays out a month with leading blanks', () => {
    const g = monthGrid('2026-10-16')         // Oct 1 2026 is a Thursday
    expect(g.slice(0, 5)).toEqual([null, null, null, null, '2026-10-01'])
    expect(g.filter(Boolean)).toHaveLength(31)
  })
  it('labels periods', () => {
    expect(periodLabel('2026-10-16', 'month')).toBe('October 2026')
    expect(periodLabel('2026-10-16', 'year')).toBe('2026')
  })
  it('compares only the elapsed part of the period', () => {
    const cur = { kwh: [40, 42, 0, 0] }, prev = { kwh: [38, 40, 50, 60] }
    expect(compareSoFar(cur, prev)).toMatchObject({ buckets: 2, kwh: 82, prevKwh: 78, pct: 5 })
    expect(compareSoFar({ kwh: [0, 0] }, prev)).toBeNull()
  })
})

describe('history regressions', () => {
  it('never navigates past today', async () => {
    const { nextDate } = await import('./history')
    expect(nextDate('2026-09-20', 'month', '2026-10-16')).toBe('2026-10-16')
    expect(nextDate('2026-08-10', 'month', '2026-10-16')).toBe('2026-09-10')
  })
  it('counts a zero-use bucket inside the elapsed period', () => {
    expect(compareSoFar({ kwh: [10, 0, 20] }, { kwh: [10, 10, 10] })).toMatchObject({ buckets: 3, pct: 0 })
  })
})
