import { describe, expect, it } from 'vitest'
import { explained, fmtSecs, newerHunt } from './hunt'

describe('hunt helpers', () => {
  it('reports how much of always-on the map explains', () => {
    const r = explained([{ l1_w: 0, l2_w: -612 }, { l1_w: -74, l2_w: 0 }, { l1_w: 5, l2_w: 0 }], 912)
    expect(r.mapped).toBe(686)
    expect(r.pct).toBe(75)
  })
  it('keeps the newer hunt view, whichever arrives first', () => {
    const a = { epoch: 1, version: 4 }, b = { epoch: 1, version: 5 }
    expect(newerHunt(a, b)).toBe(b)
    expect(newerHunt(b, a)).toBe(b)
    expect(newerHunt(null, a)).toBe(a)
    expect(newerHunt(a, null)).toBe(a)
    // a restarted server starts counting again: its views win
    expect(newerHunt({ epoch: 1, version: 90 }, { epoch: 2, version: 1 }).epoch).toBe(2)
  })
  it('formats durations', () => {
    expect(fmtSecs(8.4)).toBe('8 s')
    expect(fmtSecs(125)).toBe('2 min 5 s')
    expect(fmtSecs(120)).toBe('2 min')
    expect(fmtSecs(4320)).toBe('1 h 12 min')
  })
})
