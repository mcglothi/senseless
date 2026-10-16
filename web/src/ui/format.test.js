import { describe, expect, it } from 'vitest'
import { find240V, fmtPower, fmtSigned, legShare, repeatingSteps } from './format'
import { foldReading } from '../hooks/useLive'
import { tokensFor } from './themes'

describe('format', () => {
  it('formats power and signed steps', () => {
    expect(fmtPower(1122)).toBe('1,122 W')
    expect(fmtPower(12500)).toBe('12.50 kW')
    expect(fmtPower(null)).toBe('—')
    expect(fmtSigned(-1907)).toBe('−1,907 W')
    expect(fmtSigned(299.4)).toBe('+299 W')
  })
  it('splits legs into whole percents', () => {
    expect(legShare(89, 1033)).toEqual([8, 92])
    expect(legShare(0, 0)).toEqual([50, 50])
  })
})

describe('find240V', () => {
  it('pairs balanced two-leg steps in the same frame', () => {
    const d = [
      { leg: 1, w: 1217, frame: 10, t: 1 }, { leg: 2, w: 1232, frame: 10, t: 1 }, // 240 V on
      { leg: 1, w: 1021, frame: 20, t: 2 },                                        // one leg only
      { leg: 1, w: 500, frame: 30, t: 3 }, { leg: 2, w: -480, frame: 30, t: 3 },   // opposite signs
      { leg: 1, w: 1200, frame: 40, t: 4 }, { leg: 2, w: 400, frame: 40, t: 4 },   // unbalanced
    ]
    const v = find240V(d)
    expect(v).toHaveLength(1)
    expect(v[0]).toMatchObject({ frame: 10, w: 2449 })
  })
})

describe('repeatingSteps', () => {
  it('finds the dominant small cycler per leg', () => {
    const d = []
    for (let i = 0; i < 20; i++) d.push({ leg: 2, w: i % 2 ? 44 : -46 })
    for (let i = 0; i < 3; i++) d.push({ leg: 1, w: 120 })
    const r = repeatingSteps(d)
    expect(r[0]).toMatchObject({ leg: 2 })
    expect(r[0].count).toBeGreaterThanOrEqual(20)
    expect(r.find(x => x.leg === 1)).toBeUndefined()
  })
})

describe('foldReading', () => {
  it('averages within a bucket and trims the window', () => {
    let b = []
    b = foldReading(b, { t: 100, w: 1000, l1: 100, l2: 900 }, 5, 60)
    b = foldReading(b, { t: 101, w: 2000, l1: 200, l2: 1800 }, 5, 60)
    expect(b).toHaveLength(1)
    expect(b[0].w).toBe(1500)
    b = foldReading(b, { t: 200, w: 10, l1: 5, l2: 5 }, 5, 60)
    expect(b.map(x => x.t)).toEqual([200]) // 100 fell out of the 60 s window
    expect(foldReading(b, { t: 150, w: 1 }, 5, 60)).toBe(b) // late reading ignored
  })
})

describe('themes', () => {
  const senseless = {
    id: 'senseless', variant: 'dark', accent: 'base0A',
    palette: { base00: '#080808', base05: '#e8e8e8', base07: '#ffffff', base08: '#ff4d4f', base09: '#f97316', base0A: '#ffab00',
      base0B: '#39ff5a', base0C: '#00fff7', base0D: '#2979ff', base0E: '#bf5fff', base0F: '#ff2d6b' },
  }
  it('derives amber accent and leg colors for the default theme', () => {
    const t = tokensFor(senseless)
    expect(t.accent).toBe('#ffab00')
    expect(t.l1).toBe('#00fff7')
    expect(t.l2).toBe('#f97316')
    expect(t.scheme).toBe('dark')
  })
  it('picks leg colors by hue when a scheme puts greys in the usual slots', () => {
    // tinted-theming tokyo-night-dark: base08/09 are foreground greys
    const tokyo = { id: 'tokyo-night-dark', variant: 'dark', palette: {
      base00: '#1a1b26', base05: '#a9b1d6', base07: '#d5d6db', base08: '#c0caf5', base09: '#a9b1d6', base0A: '#0db9d7',
      base0B: '#9ece6a', base0C: '#b4f9f8', base0D: '#2ac3de', base0E: '#bb9af7', base0F: '#f7768e' } }
    const t = tokensFor(tokyo)
    expect(t.l2).toBe('#f7768e')   // the only saturated warm color
    expect(['#2ac3de', '#b4f9f8']).toContain(t.l1)
  })
  it('keeps small-label text readable (dim >= 4.5:1, faint >= 3:1 on cards)', async () => {
    const { lum } = await import('./themes')
    const cr = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05) }
    for (const th of [senseless, { ...senseless, variant: 'light', palette: { ...senseless.palette, base00: '#f7f6f2', base05: '#1d1c1a', base07: '#000000' } }]) {
      const t = tokensFor(th)
      expect(cr(t.dim, t.panel)).toBeGreaterThanOrEqual(4.5)
      expect(cr(t.faint, t.panel)).toBeGreaterThanOrEqual(3)
    }
  })
  it('uses blue for L1 on light themes', () => {
    const light = { ...senseless, variant: 'light', palette: { ...senseless.palette, base00: '#f7f6f2', base05: '#1d1c1a', base07: '#000000', base0D: '#1d4ed8' } }
    expect(tokensFor(light).l1).toBe('#1d4ed8')
  })
})
