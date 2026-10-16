// Number formatting and small, pure analyses over step events. No React here, so
// it's unit-tested (format.test.js).

export const fmtInt = n => (n == null || Number.isNaN(n) ? '—' : Math.round(n).toLocaleString('en-US'))

// 940 -> "940 W", 1840 -> "1.84 kW"
export function fmtPower(w, { kwFrom = 10000 } = {}) {
  if (w == null || Number.isNaN(w)) return '—'
  return Math.abs(w) >= kwFrom ? `${(w / 1000).toFixed(2)} kW` : `${fmtInt(w)} W`
}

export const fmtSigned = w => (w == null ? '—' : `${w > 0 ? '+' : '−'}${fmtInt(Math.abs(w))} W`)

export const fmtMoney = d => (d == null ? '—' : `$${d.toFixed(2)}`)

export function fmtClock(ts, { seconds = true } = {}) {
  return new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), hour12: false,
  })
}

// Share of the house on each leg, as whole percents that add to 100.
export function legShare(l1, l2) {
  const total = (l1 ?? 0) + (l2 ?? 0)
  if (!(total > 0)) return [50, 50]
  const a = Math.round((Math.max(0, l1) / total) * 100)
  return [a, 100 - a]
}

// 240 V appliances step both legs in the same AC cycle with about the same size.
// deltas: [{leg, w, frame}] -> [{frame, t, w}] one entry per balanced two-leg step.
export function find240V(deltas, { minW = 300, tolerance = 0.25 } = {}) {
  const byFrame = new Map()
  for (const d of deltas) {
    if (d.frame == null || Math.abs(d.w) < minW) continue
    const list = byFrame.get(d.frame) ?? []
    list.push(d)
    byFrame.set(d.frame, list)
  }
  const out = []
  for (const [frame, list] of byFrame) {
    const a = list.find(d => d.leg === 1), b = list.find(d => d.leg === 2)
    if (!a || !b || Math.sign(a.w) !== Math.sign(b.w)) continue
    const big = Math.max(Math.abs(a.w), Math.abs(b.w))
    if (Math.abs(Math.abs(a.w) - Math.abs(b.w)) / big > tolerance) continue
    out.push({ frame, t: a.t, w: a.w + b.w, l1: a.w, l2: b.w })
  }
  return out.sort((x, y) => x.frame - y.frame)
}

// The most repeated small step on each leg: the "45 W cycler" kind of load.
// Steps are binned to 5 W by absolute size; returns [{leg, w, count}] most common first.
// Steps under 30 W are mostly the monitor's noise floor, so they don't count.
export function repeatingSteps(deltas, { minCount = 8, minW = 30, maxW = 300, bin = 5 } = {}) {
  const counts = new Map()
  for (const d of deltas) {
    const a = Math.abs(d.w)
    if (a < minW || a > maxW) continue
    const key = `${d.leg}:${Math.round(a / bin) * bin}`
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  // merge each bin with its neighbours so 44 W and 46 W count together
  const rows = []
  for (const [key, n] of counts) {
    const [leg, w] = key.split(':').map(Number)
    const near = n + (counts.get(`${leg}:${w - bin}`) ?? 0) + (counts.get(`${leg}:${w + bin}`) ?? 0)
    rows.push({ leg, w, count: near })
  }
  rows.sort((a, b) => b.count - a.count)
  const picked = []
  for (const r of rows) {
    if (r.count < minCount) break
    if (picked.some(p => p.leg === r.leg && Math.abs(p.w - r.w) <= 2 * bin)) continue
    picked.push(r)
  }
  return picked
}
