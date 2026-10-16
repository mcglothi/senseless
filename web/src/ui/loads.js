// Pure helpers for load patterns: fingerprint geometry, naming ideas, formatting. Tested in loads.test.js.

export function fmtDuration(s) {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.round(s / 60)} min`
  return `${(s / 3600).toFixed(1)} h`
}

export const legsLabel = legs => (legs === 'both' ? '240 V' : legs)

// Staircase path for a pattern's example run in a w x h box: each stage steps up, the run
// holds for its typical duration, then steps off.
export function fingerprintPath(p, w = 96, h = 48, pad = 6) {
  const stages = p.example?.stages?.length ? p.example.stages : [[0, p.center_w]]
  const total = stages.reduce((s, [, x]) => s + Math.max(0, x), 0) || p.center_w || 1
  const dur = Math.max(1, p.median_duration_s ?? p.example?.duration_s ?? 10)
  const lastStage = stages[stages.length - 1][0] / 60
  // one shared log time axis (0 s .. 6 h) for every pattern, so width means duration: a 13 s
  // pulse is narrow, a 20 min run wide. On at 10%.
  const span = Math.log10(1 + 6 * 3600)
  const X = t => pad + (0.1 + 0.85 * Math.min(1, Math.log10(1 + t) / span)) * (w - 2 * pad)
  const Y = v => h - pad - (v / total) * (h - 2 * pad)
  let d = `M${pad},${Y(0).toFixed(1)}`, level = 0
  for (const [frame, x] of stages) {
    const xt = X(frame / 60).toFixed(1)
    d += ` L${xt},${Y(level).toFixed(1)}`
    level += Math.max(0, x)
    d += ` L${xt},${Y(level).toFixed(1)}`
  }
  const off = p.median_duration_s == null && !p.closed_runs ? null : X(lastStage + dur).toFixed(1)
  if (off == null) return d + ` L${w - pad},${Y(level).toFixed(1)}`  // never seen it turn off
  return d + ` L${off},${Y(level).toFixed(1)} L${off},${Y(0).toFixed(1)} L${w - pad},${Y(0).toFixed(1)}`
}

// Naming ideas from size, legs, stages and duration. Only suggestions: you know your house.
export function nameIdeas(p) {
  const w = p.center_w, dur = p.median_duration_s ?? 0, multi = (p.example?.stages?.length ?? 1) > 1
  if (p.legs === 'both') {
    if (w > 3500) return ['Electric range', 'EV charger', 'Water heater']
    if (w > 1500) return ['Dryer', 'Heat pump', 'Water heater']
    return ['A/C compressor', 'Well pump', 'Heat pump fan']
  }
  if (w >= 1200) return multi ? ['Microwave', 'Space heater', 'Hair dryer'] : ['Kettle', 'Toaster', 'Space heater']
  if (w >= 600) return dur > 600 ? ['Dishwasher heat', 'Dehumidifier', 'Space heater'] : ['Coffee maker', 'Vacuum', 'Microwave (low)']
  if (w >= 250) return dur > 120 ? ['Refrigerator', 'Dehumidifier', 'Sump pump'] : ['Garage door', 'Sump pump', 'Blender']
  if (w >= 80) return dur > 600 ? ['Freezer', 'Furnace blower', 'TV'] : ['Furnace inducer', 'Bathroom fan', 'Lights']
  return dur < 60 ? ['Relay / cycling device', 'Aquarium heater', 'Printer'] : ['Lights', 'Router / network', 'Fan']
}

// Named patterns grouped by name (case-insensitive): one device often shows up as two patterns when
// its step size varies (47 W and 56 W). Stats combine; the biggest pattern draws the fingerprint.
export function groupNamed(patterns) {
  const groups = new Map()
  for (const p of patterns) {
    if (!p.name) continue
    const key = p.name.trim().toLowerCase()
    const g = groups.get(key)
    if (!g) { groups.set(key, { ...p, members: [p] }); continue }
    g.members.push(p)
    g.runs_window += p.runs_window
    g.runs_today = (g.runs_today ?? 0) + (p.runs_today ?? 0)
    g.wh_today += p.wh_today
    g.running = g.running || p.running
    if (p.center_w > g.center_w) Object.assign(g, { id: p.id, center_w: p.center_w, example: p.example, median_duration_s: p.median_duration_s, closed_runs: p.closed_runs })
  }
  return [...groups.values()].sort((a, b) => b.wh_today - a.wh_today || b.runs_window - a.runs_window)
}

// Names already used for loads on the same legs: offered first, so a split device gets one name.
export function existingNames(patterns, p, max = 2, tol = 0.35) {
  const seen = new Set()
  return patterns
    .filter(x => x.name && x.legs === p.legs && x.id !== p.id && Math.abs(x.center_w - p.center_w) <= tol * Math.max(x.center_w, p.center_w))
    .sort((a, b) => Math.abs(a.center_w - p.center_w) - Math.abs(b.center_w - p.center_w))
    .map(x => x.name)
    .filter(n => !seen.has(n.toLowerCase()) && seen.add(n.toLowerCase()))
    .slice(0, max)
}

// Inbox: unnamed, not ignored, repeated enough to be worth naming. Patterns Sense has a hint for
// first (one-tap wins), then most energy over the 48 h window (today's is ~0 just after midnight).
export function inboxOf(patterns, minRuns = 3) {
  return patterns
    // fragments of a washer/dishwasher program belong to its cycle (Loads > Cycles), not the inbox
    .filter(p => !p.name && !p.ignored && !p.part_of_cycles && (p.runs_window >= minRuns || (p.legs === 'both' && p.runs_window >= 1)))
    .sort((a, b) => (b.sense_hint ? 1 : 0) - (a.sense_hint ? 1 : 0) || (b.wh_window ?? b.wh_today) - (a.wh_window ?? a.wh_today) || b.runs_window - a.runs_window)
}

// Naming ideas for a program cycle, from legs, heating and length.
export function cycleIdeas(p, last) {
  const heat = last?.has_heat, mins = (p.median_duration_s ?? p.duration_s ?? 0) / 60
  if (p.legs === 'both') return heat ? ['Dryer', 'Electric range', 'Heat pump'] : ['Well pump', 'Heat pump', 'Dryer']
  if (heat) return ['Dishwasher', 'Washer (hot wash)', 'Coffee maker']
  return mins >= 25 ? ['Washer', 'Dishwasher', 'Dehumidifier'] : ['Washer (quick)', 'Sump pump', 'Bread maker']
}
