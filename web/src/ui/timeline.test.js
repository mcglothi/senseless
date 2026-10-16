import { describe, expect, it } from 'vitest'
import { minRunW, packRows, ticksFor, zoomBucket } from './timeline'

describe('timeline helpers', () => {
  it('sizes buckets to ~600 per window, at least 1 s', () => {
    expect(zoomBucket(86400)).toBe(144)
    expect(zoomBucket(120)).toBe(1)
  })
  it('places ticks on round times, 6-8 across', () => {
    const start = 1_800_000_000, span = 10800
    const ticks = ticksFor(span, start, start + span)
    expect(ticks.length).toBeGreaterThanOrEqual(5)
    expect(ticks.length).toBeLessThanOrEqual(8)
    const gaps = ticks.slice(1).map((t, i) => t.t - ticks[i].t)
    expect(new Set(gaps).size).toBe(1)
    expect(ticks.every(t => t.t >= start && t.t <= start + span)).toBe(true)
  })
  it('packs overlapping runs into rows, biggest first, and caps rows', () => {
    const runs = [
      { start_ts: 0, end_ts: 100, w: 50 }, { start_ts: 50, end_ts: 150, w: 2000 }, { start_ts: 120, end_ts: 130, w: 40 },
      { start_ts: 60, end_ts: 70, w: 45 }, { start_ts: 65, end_ts: 66, w: 30 },
    ]
    const p = packRows(runs, 1000, 3)
    const rowOf = s => p.find(x => x.run.start_ts === s)?.row
    expect(rowOf(50)).toBe(0)           // the 2 kW run gets the first row
    expect(rowOf(0)).toBe(1)
    expect(rowOf(60)).toBe(2)
    expect(rowOf(120)).toBe(1)          // fits in row 1 after the 50 W run ended
    expect(rowOf(65)).toBeUndefined()   // no 4th row; smallest dropped
  })
  it('keeps hour ticks on local multiples across a DST change', () => {
    const prev = process.env.TZ
    process.env.TZ = 'America/New_York'
    try {
      const start = new Date('2026-11-01T00:00:00-04:00').getTime() / 1000   // DST ends 02:00 that night
      const labels = ticksFor(86400, start, start + 86400).map(x => x.label)
      expect(labels.slice(0, 4)).toEqual(['00:00', '03:00', '06:00', '09:00'])
    } finally { process.env.TZ = prev }
  })
  it('gives near-zero-length runs a minimum width when packing', () => {
    const runs = [{ start_ts: 0, end_ts: null, w: 100 }, { start_ts: 1, end_ts: null, w: 90 }]
    expect(packRows(runs, 0, 4).map(x => x.row)).toEqual([0, 0])            // overlap ignored at zero width
    expect(packRows(runs, 0, 4, 60).map(x => x.row)).toEqual([0, 1])        // 60 s min width: separate rows
  })
  it('filters small runs at wide zooms', () => {
    expect(minRunW(10800)).toBe(100)
    expect(minRunW(120)).toBe(30)
  })
  it('treats running runs as extending to now', () => {
    const p = packRows([{ start_ts: 0, end_ts: null, running: true }, { start_ts: 500, end_ts: 600 }], 1000, 4)
    expect(p.map(x => x.row)).toEqual([0, 1])
  })
})
