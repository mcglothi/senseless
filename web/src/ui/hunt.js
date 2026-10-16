// Breaker Hunt math, pure so it's unit-tested (hunt.test.js).

const avg = xs => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)

// Average per-leg power over readings [{t, l1, l2}] within the last `seconds`.
export function legAverage(readings, seconds, now) {
  const recent = readings.filter(r => r.t >= now - seconds)
  return { l1: avg(recent.map(r => r.l1)), l2: avg(recent.map(r => r.l2)), n: recent.length }
}

// What changed since the baseline: the monitor's own steps (exact, cycle-accurate) and the smoothed
// leg-power change (fallback when steps were below the noise floor or missed).
export function huntDrop(baseline, current, deltas) {
  const since = deltas.filter(d => d.t >= baseline.t)
  const stepSum = leg => since.filter(d => d.leg === leg).reduce((a, d) => a + d.w, 0)
  const steps = { l1: stepSum(1), l2: stepSum(2), count: since.length }
  const change = {
    l1: current.l1 != null && baseline.l1 != null ? current.l1 - baseline.l1 : null,
    l2: current.l2 != null && baseline.l2 != null ? current.l2 - baseline.l2 : null,
  }
  // prefer steps when they explain most of the measured change (or the change is noise-level)
  const pick = leg => {
    const s = steps[leg], c = change[leg]
    if (c == null) return s
    if (Math.abs(c) < 15) return Math.abs(s) >= 15 ? s : 0
    return Math.abs(s) >= Math.abs(c) * 0.6 ? s : c
  }
  const l1 = pick('l1'), l2 = pick('l2')
  const both = Math.abs(l1) >= 100 && Math.abs(l2) >= 100 && Math.sign(l1) === Math.sign(l2) &&
    Math.abs(Math.abs(l1) - Math.abs(l2)) / Math.max(Math.abs(l1), Math.abs(l2)) <= 0.25
  return { l1, l2, total: l1 + l2, steps, change, both, meaningful: Math.abs(l1 + l2) >= 15 }
}

// How much of the always-on baseline the mapped breakers account for (drops are negative watts).
export function explained(circuits, alwaysOnW) {
  const mapped = circuits.reduce((a, c) => a - Math.min(0, c.l1_w) - Math.min(0, c.l2_w), 0)
  return { mapped, pct: alwaysOnW ? Math.min(100, Math.round((mapped / alwaysOnW) * 100)) : null }
}
