import { describe, expect, it } from 'vitest'
import { fingerprintPath, fmtDuration, inboxOf, nameIdeas } from './loads'
import { matchStep } from '../hooks/useLoads'

const mw = { id: 1, legs: 'L2', center_w: 2025, median_duration_s: 29.5, closed_runs: 4, runs_window: 4, wh_today: 30,
  example: { stages: [[0, 299], [90, 1726]] } }

describe('loads helpers', () => {
  it('formats durations', () => {
    expect(fmtDuration(13)).toBe('13 s')
    expect(fmtDuration(157)).toBe('3 min')
    expect(fmtDuration(5400)).toBe('1.5 h')
    expect(fmtDuration(null)).toBe('—')
  })
  it('draws a staircase that steps up per stage and back down', () => {
    const d = fingerprintPath(mw)
    const ys = [...d.matchAll(/,(\d+(\.\d+)?)/g)].map(m => +m[1])
    expect(Math.min(...ys)).toBeLessThan(10)   // reaches the top
    expect(ys[ys.length - 1]).toBe(42)          // ends at zero (48 - pad 6)
    expect(d.split('L').length).toBeGreaterThan(6)
  })
  it('makes width mean duration on a shared axis', () => {
    const offX = p => +fingerprintPath(p).split(' L').slice(-2)[0].split(',')[0]
    const short = offX({ ...mw, median_duration_s: 13, example: null })
    const long = offX({ ...mw, median_duration_s: 1200, example: null })
    expect(long - short).toBeGreaterThan(20)
  })
  it('leaves never-closed patterns on at the right edge', () => {
    const d = fingerprintPath({ ...mw, median_duration_s: null, closed_runs: 0 })
    const lastY = +d.split(',').pop()
    expect(lastY).toBeLessThan(42)              // still on at the right edge, never drops to zero
  })
  it('suggests names by size and legs', () => {
    expect(nameIdeas(mw)).toContain('Microwave')
    expect(nameIdeas({ legs: 'both', center_w: 2449, median_duration_s: 180 })).toContain('Dryer')
    expect(nameIdeas({ legs: 'L2', center_w: 45, median_duration_s: 5 })[0]).toMatch(/cycling/i)
  })
  it('builds the inbox from unnamed repeating patterns, 240 V even once', () => {
    const ps = [mw, { ...mw, id: 2, name: 'Kettle' }, { ...mw, id: 3, ignored: 1 }, { ...mw, id: 4, runs_window: 1 },
      { id: 5, legs: 'both', center_w: 2449, runs_window: 1, wh_today: 100 }]
    expect(inboxOf(ps).map(p => p.id)).toEqual([5, 1])
  })
  it('matches a step to the nearest pattern on the same legs', () => {
    const ps = [{ id: 1, legs: 'L2', center_w: 45 }, { id: 2, legs: 'L1', center_w: 45 }, { id: 3, legs: 'both', center_w: 2449 }]
    expect(matchStep(ps, { leg: 2, w: -47 })?.id).toBe(1)
    expect(matchStep(ps, { leg: 2, w: 60 })).toBeNull()
    expect(matchStep(ps, { leg: 1, w: 2400 }, true)?.id).toBe(3)
  })
})
