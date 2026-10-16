// Pure timeline helpers: bucket size per zoom, axis ticks, packing runs into rows. Tested in timeline.test.js.

// About 600 buckets across the window, never finer than 1 s (the feed is ~2 Hz).
export const zoomBucket = span => Math.max(1, Math.round(span / 600))

const STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600]

// Round-time axis ticks: aim for ~6–8 labels across the window, on local wall-clock multiples.
// Hour-sized steps walk the local clock (setHours), so a DST change doesn't knock them off 00/03/06…
export function ticksFor(span, start, end) {
  const step = STEPS.find(s => span / s <= 8) ?? 21600
  const out = []
  const label = d => d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
  if (step >= 3600) {
    const h = step / 3600
    const d = new Date(start * 1000)
    d.setMinutes(0, 0, 0)
    d.setHours(Math.ceil((d.getHours() + (d.getTime() < start * 1000 ? 1 : 0)) / h) * h)
    for (; d.getTime() / 1000 <= end; d.setHours(d.getHours() + h)) {
      if (d.getHours() % h) d.setHours(Math.ceil(d.getHours() / h) * h)  // re-align after a DST jump
      if (d.getTime() / 1000 >= start && d.getTime() / 1000 <= end) out.push({ t: d.getTime() / 1000, label: label(d) })
    }
    return out
  }
  // minute steps: DST moves clocks by whole hours, so fixed-second steps stay aligned
  const offset = new Date(start * 1000).getTimezoneOffset() * 60
  for (let t = Math.ceil((start - offset) / step) * step + offset; t <= end; t += step) out.push({ t, label: label(new Date(t * 1000)) })
  return out
}

// Interval packing, biggest runs first: each run takes the first row it doesn't overlap, so
// large loads always get a row and small ones fill the gaps. Runs that don't fit in maxRows
// are left out of the drawing.
// minDurS: the time a minimum-width bar covers at this zoom, so tiny runs still get room.
export function packRows(runs, now, maxRows = 4, minDurS = 0) {
  const rows = []   // per row: [[start, end], ...]
  const out = []
  const endOf = r => r.end_ts ?? (r.running ? now : r.start_ts)
  const sorted = [...runs].sort((a, b) => (b.w ?? 0) - (a.w ?? 0) || a.start_ts - b.start_ts)
  for (const r of sorted) {
    const s = r.start_ts, e = Math.max(endOf(r), s + Math.max(1e-6, minDurS))
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
