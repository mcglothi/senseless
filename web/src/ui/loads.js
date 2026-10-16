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

// Inbox: unnamed, not ignored, repeated enough to be worth naming. Most energy first.
export function inboxOf(patterns, minRuns = 3) {
  return patterns
    .filter(p => !p.name && !p.ignored && (p.runs_window >= minRuns || (p.legs === 'both' && p.runs_window >= 1)))
    .sort((a, b) => b.wh_today - a.wh_today || b.runs_window - a.runs_window)
}
