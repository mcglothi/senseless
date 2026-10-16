// Pure timeline helpers: bucket size per zoom, axis ticks, packing runs into rows. Tested in timeline.test.js.

// About 600 buckets across the window, never finer than 1 s (the feed is ~2 Hz).
export const zoomBucket = span => Math.max(1, Math.round(span / 600))

const STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600]

// Round-time axis ticks: aim for ~6–8 labels across the window.
export function ticksFor(span, start, end) {
  const step = STEPS.find(s => span / s <= 8) ?? 21600
  const out = []
  // align to local wall-clock multiples of step
  const offset = new Date(start * 1000).getTimezoneOffset() * 60
  let t = Math.ceil((start - offset) / step) * step + offset
  for (; t <= end; t += step) {
    const d = new Date(t * 1000)
    out.push({ t, label: d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false }) })
  }
  return out
}

// Interval packing, biggest runs first: each run takes the first row it doesn't overlap, so
// large loads always get a row and small ones fill the gaps. Runs that don't fit in maxRows
// are left out of the drawing.
export function packRows(runs, now, maxRows = 4) {
  const rows = []   // per row: [[start, end], ...]
  const out = []
  const endOf = r => r.end_ts ?? (r.running ? now : r.start_ts)
  const sorted = [...runs].sort((a, b) => (b.w ?? 0) - (a.w ?? 0) || a.start_ts - b.start_ts)
  for (const r of sorted) {
    const s = r.start_ts, e = Math.max(endOf(r), s + 1e-6)
    let row = rows.findIndex(iv => iv.every(([a, b]) => e <= a || s >= b))
    if (row === -1) {
      if (rows.length >= maxRows) continue
      row = rows.length
      rows.push([])
    }
    rows[row].push([s, e])
    out.push({ run: r, row })
  }
  return out
}

// Which runs to draw at a zoom: small ones are noise at wide zooms, unless you've named them.
export function minRunW(span) {
  return span >= 3600 ? 100 : span >= 900 ? 50 : 30
}
