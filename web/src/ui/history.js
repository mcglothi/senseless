// Pure date math for the History view (local dates as YYYY-MM-DD strings). Tested in history.test.js.

export const pad = n => String(n).padStart(2, '0')
export const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
export const parse = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d) }
export const todayIso = () => iso(new Date())

// Move a date by one period of the scale (clamping the day so Jan 31 + 1 month = Feb 28).
export function shift(dateStr, scale, dir) {
  const d = parse(dateStr)
  if (scale === 'day') d.setDate(d.getDate() + dir)
  else if (scale === 'month') { const day = d.getDate(); d.setDate(1); d.setMonth(d.getMonth() + dir); d.setDate(Math.min(day, daysIn(d))) }
  else d.setFullYear(d.getFullYear() + dir)
  return iso(d)
}

// First day of the period containing dateStr.
export function periodStart(dateStr, scale) {
  const [y, m] = dateStr.split('-')
  return scale === 'day' ? dateStr : scale === 'month' ? `${y}-${m}-01` : `${y}-01-01`
}

// Is there a later period with data yet? (the next period must have started by today)
export const hasNext = (dateStr, scale, today) => periodStart(shift(dateStr, scale, 1), scale) <= today

// The date "next" moves to: one period on, but never past today (the backend refuses future dates).
export const nextDate = (dateStr, scale, today) => { const n = shift(dateStr, scale, 1); return n > today ? today : n }

export const daysIn = d => new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()

// Calendar grid for a month: leading blanks so day 1 lands on its weekday (Sunday first).
export function monthGrid(dateStr) {
  const d = parse(dateStr)
  const first = new Date(d.getFullYear(), d.getMonth(), 1)
  const cells = Array.from({ length: first.getDay() }, () => null)
  for (let i = 1; i <= daysIn(d); i++) cells.push(iso(new Date(d.getFullYear(), d.getMonth(), i)))
  return cells
}

export function periodLabel(dateStr, scale) {
  const d = parse(dateStr)
  if (scale === 'day') return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  if (scale === 'month') return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  return String(d.getFullYear())
}

// Fair comparison with the previous period: only as many buckets as the current one has so far.
export function compareSoFar(cur, prev) {
  if (!cur?.kwh?.length || !prev?.kwh?.length) return null
  // elapsed buckets: up to the last one with data (a zero-use bucket in between still counts)
  let n = 0
  cur.kwh.forEach((x, i) => { if (x != null && x > 0) n = i + 1 })
  if (!n) return null
  const sum = xs => xs.slice(0, n).reduce((a, b) => a + (b ?? 0), 0)
  const a = sum(cur.kwh), b = sum(prev.kwh)
  return b > 0 ? { buckets: n, kwh: a, prevKwh: b, pct: Math.round(((a - b) / b) * 100) } : null
}
