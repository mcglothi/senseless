import { describe, expect, it } from 'vitest'
import { explained, huntDrop, legAverage } from './hunt'

describe('breaker hunt', () => {
  it('averages leg power over a recent window', () => {
    const rs = [{ t: 1, l1: 100, l2: 1000 }, { t: 9, l1: 110, l2: 1020 }, { t: 10, l1: 90, l2: 980 }]
    expect(legAverage(rs, 5, 10)).toMatchObject({ l1: 100, l2: 1000, n: 2 })
  })
  it('uses the exact steps when they explain the change', () => {
    const base = { t: 100, l1: 90, l2: 1030 }
    const d = huntDrop(base, { l1: 88, l2: 425 }, [{ t: 105, leg: 2, w: -612.4 }, { t: 50, leg: 2, w: -40 }])
    expect(d.l2).toBeCloseTo(-612.4)   // steps (not the noisier -605 change), and only since the baseline
    expect(d.l1).toBe(0)               // -2 W change on L1 is noise
    expect(d.meaningful).toBe(true)
    expect(d.both).toBe(false)
  })
  it('ignores a step that arrived with the baseline', () => {
    const d = huntDrop({ t: 100, l1: 90, l2: 1030 }, { l1: 90, l2: 1030 }, [{ t: 100, leg: 2, w: -600 }])
    expect(d.meaningful).toBe(false)
  })
  it('uses the measured change when steps point the other way', () => {
    const d = huntDrop({ t: 0, l1: 100, l2: 1600 }, { l1: 100, l2: 1000 }, [{ t: 1, leg: 2, w: 400 }])
    expect(d.l2).toBe(-600)
  })
  it('excludes readings timestamped after now', () => {
    expect(legAverage([{ t: 9, l1: 100, l2: 0 }, { t: 50, l1: 900, l2: 0 }], 5, 10).l1).toBe(100)
  })
  it('falls back to the leg change when steps were missed', () => {
    const d = huntDrop({ t: 0, l1: 100, l2: 900 }, { l1: 100, l2: 600 }, [])
    expect(d.l2).toBe(-300)
  })
  it('flags 240 V breakers (balanced drop on both legs)', () => {
    const d = huntDrop({ t: 0, l1: 1300, l2: 1300 }, { l1: 80, l2: 70 },
      [{ t: 1, leg: 1, w: -1217 }, { t: 1, leg: 2, w: -1232 }])
    expect(d.both).toBe(true)
    expect(d.total).toBeCloseTo(-2449)
  })
  it('reports how much of always-on the map explains', () => {
    const r = explained([{ l1_w: 0, l2_w: -612 }, { l1_w: -74, l2_w: 0 }, { l1_w: 5, l2_w: 0 }], 912)
    expect(r.mapped).toBe(686)
    expect(r.pct).toBe(75)
  })
})
