import { describe, expect, it } from 'vitest'
import { breakdown, cycleBars, describeComponents, touAdvice, usd } from './billing'

describe('bill helpers', () => {
  it('formats dollars', () => {
    expect(usd(336.771)).toBe('$336.77')
    expect(usd(1234.5)).toBe('$1,234.50')
    expect(usd(null)).toBe('—')
  })
  it('splits a bill into shares, skipping empty parts', () => {
    const b = breakdown({ fixed: 25, delivery: 25, supply: 50, tax: 0 })
    expect(b.map(p => p.key)).toEqual(['fixed', 'delivery', 'supply'])
    expect(b.map(p => p.pct)).toEqual([25, 25, 50])
    expect(breakdown(null)).toEqual([])
  })
  it('fills the cycle with actual then projected days', () => {
    const t = new Date()
    const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const y = new Date(t); y.setDate(t.getDate() - 1)
    const bars = cycleBars({ start: iso(y), days: 4, kwh_per_day: 40, daily: [{ date: iso(y), kwh: 38 }, { date: iso(t), kwh: 12 }] })
    expect(bars.map(b => b.kind)).toEqual(['actual', 'today', 'projected', 'projected'])
    expect(bars.map(b => b.kwh)).toEqual([38, 12, 40, 40])
  })
  it('explains time-of-use against the 86% rule', () => {
    const a = touAdvice({ on_peak_share: 0.097, compare: [{ plan: 'cmp-a-tou', delta: -25.65 }] })
    expect(a.saves).toBe(true)
    expect(a.off).toBe(90)
    expect(a.text).toContain('$25.65')
    expect(touAdvice({ on_peak_share: 0.3, compare: [{ plan: 'cmp-a-tou', delta: 12 }] }).saves).toBe(false)
    expect(touAdvice({ on_peak_share: null, compare: [] })).toBe(null)
  })
  it('describes tariff components in plain words', () => {
    expect(describeComponents([{ type: 'fixed', per: 'month', amount: 27.37, includes_kwh: 50 }, { type: 'energy', rate: 0.11959 }]))
      .toBe('$27.37/month (includes 50 kWh) + 11.96¢/kWh.')
    expect(describeComponents([{ type: 'tou', periods: [{ name: 'on-peak', rate: 0.428836, weekdays: [0, 1, 2, 3, 4], hours: [17, 21] }], default_rate: 0.06147 }]))
      .toBe('42.88¢ on-peak (5 pm–9 pm weekdays), 6.147¢ other hours.')
    expect(describeComponents([{ type: 'credit', min_kwh: 1000, amount: -100 }])).toBe('$100.00 credit at 1000+ kWh.')
  })
})
