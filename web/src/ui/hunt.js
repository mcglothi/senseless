// Hunt helpers, pure so they're unit-tested (hunt.test.js). The measuring itself happens on the
// server (senseless/hunt.py), so the app and a voice assistant share one session.

// How much of the always-on baseline the mapped breakers account for (drops are negative watts).
export function explained(circuits, alwaysOnW) {
  const mapped = circuits.reduce((a, c) => a - Math.min(0, c.l1_w) - Math.min(0, c.l2_w), 0)
  return { mapped, pct: alwaysOnW ? Math.min(100, Math.round((mapped / alwaysOnW) * 100)) : null }
}

// The newer of two hunt views (a fetch reply and a pushed event can arrive in either order). A new
// server process (epoch) always wins: its versions start over.
export function newerHunt(a, b) {
  if (!a || !b) return a ?? b ?? null
  if (a.epoch !== b.epoch) return a.epoch > b.epoch ? a : b
  return b.version >= a.version ? b : a
}

// "8 s", "2 min 5 s", "1 h 12 min"
export function fmtSecs(s) {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 >= 1 ? ` ${Math.round(s % 60)} s` : ''}`
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`
}
