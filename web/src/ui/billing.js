// Bill helpers, pure so they're unit-tested (billing.test.js).

export const usd = n => (n == null ? '—' : `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)

// The bill's parts as shares of the total, for the stacked bar (tiny parts still get a sliver).
export function breakdown(bill) {
  if (!bill) return []
  const parts = [
    { key: 'fixed', label: 'Fixed charge', value: bill.fixed },
    { key: 'delivery', label: 'Delivery', value: bill.delivery },
    { key: 'supply', label: 'Supply', value: bill.supply },
    { key: 'tax', label: 'Sales tax', value: bill.tax },
  ].filter(p => p.value > 0)
  const total = parts.reduce((a, p) => a + p.value, 0)
  return parts.map(p => ({ ...p, pct: total ? (p.value / total) * 100 : 0 }))
}

// Daily bars for the cycle: actual days, then the projected rest at the recent daily rate.
export function cycleBars(cycle) {
  if (!cycle) return []
  const byDate = new Map(cycle.daily.map(d => [d.date, d.kwh]))
  const out = []
  const start = new Date(`${cycle.start}T12:00:00`)
  const today = new Date()
  today.setHours(12, 0, 0, 0)
  for (let i = 0; i < cycle.days; i++) {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const kwh = byDate.get(iso)
    const isToday = d.getTime() === today.getTime()
    out.push(kwh != null && d <= today
      ? { date: iso, kwh, kind: isToday ? 'today' : 'actual' }
      : { date: iso, kwh: cycle.kwh_per_day, kind: 'projected' })
  }
  return out
}

// Plain-language verdict on time-of-use, from CMP's own rule of thumb (save if ≥ 86% is off-peak).
export function touAdvice(cycle) {
  const tou = cycle?.compare?.find(c => c.plan === 'cmp-a-tou')
  if (!tou || cycle.on_peak_share == null) return null
  const off = Math.round((1 - cycle.on_peak_share) * 100)
  const saves = tou.delta < 0
  return {
    saves, off, delta: tou.delta,
    text: saves
      ? `${off}% of your use is off-peak (CMP's break-even is about 86%), so time-of-use would save about ${usd(-tou.delta)} this cycle.`
      : `${off}% of your use is off-peak; time-of-use pays off from about 86%, so it would cost ${usd(tou.delta)} more this cycle.`,
  }
}
