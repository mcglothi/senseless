import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { fmtInt } from '../../ui/format'
import { usd } from '../../ui/billing'

// What imported meter data and bills say: a year of rate plans, usage against temperature, how well
// Sense agrees with the utility's meter, and every bill checked against the model.

const card = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: 18, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }
const kicker = { fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color: C.accent }
const small = { fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.6 }
const monthLabel = m => new Date(`${m}-15T12:00:00`).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })

export function BillInsights({ insights, labelOf, currentPlan }) {
  const { isMobile, sp } = useTheme()
  if (!insights) return null
  const { plans_12mo: plans, weather, sense_vs_meter: svm, bills } = insights
  if (!plans && !weather && !svm && !bills?.length) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0, 1fr) minmax(0, 1fr)', gap: sp(14) }}>
      {plans && <YearOfPlans plans={plans} labelOf={labelOf} currentPlan={currentPlan} />}
      {weather?.bins?.length > 0 && <Weather bins={weather.bins} />}
      {bills?.length > 0 && <BillHistory bills={bills} />}
      {svm && <SenseVsMeter svm={svm} />}
    </div>
  )
}

function YearOfPlans({ plans, labelOf, currentPlan }) {
  const ids = Object.keys(plans.totals)
  const base = plans.totals[currentPlan] ?? Math.max(...Object.values(plans.totals))
  const best = ids.reduce((a, b) => (plans.totals[b] < plans.totals[a] ? b : a), ids[0])
  const maxMonth = Math.max(...plans.months.flatMap(m => Object.values(m.totals)))
  return (
    <section style={{ ...card, gridColumn: '1 / -1' }} aria-label="A year of your usage on each plan">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
        <div style={kicker}>Last 12 months on each plan</div>
        <span style={small}>{monthLabel(plans.months[0].month)} – {monthLabel(plans.months.at(-1).month)} · your meter's hourly data · today's rates</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(170px, 1fr))`, gap: 10 }}>
        {ids.map(id => {
          const d = plans.totals[id] - base
          return (
            <div key={id} style={{ border: `1px solid ${id === best ? alpha(C.green, '70') : id === currentPlan ? alpha(C.accent, '70') : C.line2}`, borderRadius: 12, padding: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: C.bright }}>{labelOf(id)}</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: C.bright, fontVariantNumeric: 'tabular-nums' }}>{usd(plans.totals[id])}<span style={{ fontSize: 12, color: C.muted }}> /yr</span></div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: id === currentPlan ? C.accent : d < -1 ? C.green : d > 1 ? C.amber : C.muted }}>
                {id === currentPlan ? 'your plan' : Math.abs(d) < 1 ? 'same' : `${d < 0 ? 'saves' : 'costs'} ${usd(Math.abs(d))}${d > 0 ? ' more' : ''}`}
              </div>
            </div>
          )
        })}
      </div>
      <div role="img" aria-label="Monthly cost on each plan" style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 120 }}>
        {plans.months.map(m => (
          <div key={m.month} style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'stretch', gap: 3 }}>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 1, height: 96 }}>
              {ids.map((id, k) => (
                <div key={id} title={`${monthLabel(m.month)} ${labelOf(id)}: ${usd(m.totals[id])} · ${fmtInt(m.kwh)} kWh · ${Math.round(m.on_peak_share * 100)}% on-peak`}
                  style={{ flex: 1, height: `${(m.totals[id] / maxMonth) * 100}%`, borderRadius: '2px 2px 0 0',
                    background: id === best ? C.green : id === currentPlan ? C.accent : [C.muted, C.l1, C.orange][k % 3], opacity: id === best || id === currentPlan ? 0.9 : 0.5 }} />
              ))}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, textAlign: 'center' }}>{monthLabel(m.month).split(' ')[0]}</div>
          </div>
        ))}
      </div>
      <p style={small}>Each month priced with that month's real hours, so time-of-use reflects winter evenings, not one quiet week.</p>
    </section>
  )
}

function Weather({ bins }) {
  const max = Math.max(...bins.map(b => b.kwh_per_day))
  const low = bins.reduce((a, b) => (b.kwh_per_day < a.kwh_per_day ? b : a), bins[0])
  return (
    <section style={card} aria-label="Usage against outdoor temperature">
      <div style={kicker}>Usage vs outdoor temperature</div>
      <div role="img" aria-label="kWh per day by temperature" style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 130 }}>
        {bins.map(b => (
          <div key={b.from_f} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }} title={`${b.from_f}–${b.to_f}°F: ${b.kwh_per_day.toFixed(1)} kWh/day over ${b.days} days`}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.body }}>{Math.round(b.kwh_per_day)}</span>
            <div style={{ width: '100%', height: `${(b.kwh_per_day / max) * 92}px`, borderRadius: '3px 3px 0 0',
              background: b.from_f < 40 ? C.l1 : b.from_f >= 70 ? C.orange : alpha(C.accent, 'a0'), opacity: b.days < 10 ? 0.45 : 1 }} />
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>{b.from_f}°</span>
          </div>
        ))}
      </div>
      <p style={small}>kWh per day by the day's average temperature. Lowest around {low.from_f}–{low.to_f}°F ({Math.round(low.kwh_per_day)} kWh/day); the rise on either side is heating and cooling. Faded: under 10 days of data.</p>
    </section>
  )
}

function BillHistory({ bills }) {
  const sorted = [...bills].sort((a, b) => a.start.localeCompare(b.start))
  const max = Math.max(...sorted.map(b => b.amount))
  const checked = sorted.filter(b => b.check)
  const exact = checked.filter(b => Math.abs(b.check.diff) <= 0.05).length
  return (
    <section style={card} aria-label="Your bills">
      <div style={kicker}>Your bills</div>
      <div role="img" aria-label="Bill amounts over time" style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 110 }}>
        {sorted.map(b => (
          <div key={b.start} title={`${b.start} → ${b.end}: ${usd(b.amount)} · ${fmtInt(b.kwh)} kWh · ${b.effective_cents_per_kwh}¢/kWh${b.check ? ` · model ${usd(b.check.model_total)}` : ''}`}
            style={{ flex: 1, minWidth: 0, height: `${(b.amount / max) * 100}%`, borderRadius: '2px 2px 0 0',
              background: b.check && Math.abs(b.check.diff) > 0.05 ? C.amber : alpha(C.accent, 'b0') }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 10, color: C.dim }}>
        <span>{monthLabel(sorted[0].start.slice(0, 7))}</span><span>{monthLabel(sorted.at(-1).start.slice(0, 7))}</span>
      </div>
      <p style={small}>
        {sorted.length} bills, {usd(Math.min(...sorted.map(b => b.amount)))}–{usd(max)}.
        {checked.length > 0 && ` The bill model reproduces ${exact} of ${checked.length} to within 5¢ from each bill's own rates.`}
      </p>
    </section>
  )
}

function SenseVsMeter({ svm }) {
  const pct = (svm.ratio - 1) * 100
  return (
    <section style={card} aria-label="Sense against your utility meter">
      <div style={kicker}>Sense vs your utility meter</div>
      <div style={{ fontSize: 32, fontWeight: 700, color: Math.abs(pct) < 2 ? C.green : C.amber }}>
        {Math.abs(pct) < 0.05 ? 'Same' : `${pct > 0 ? '+' : '−'}${Math.abs(pct).toFixed(1)}%`}
      </div>
      <p style={small}>Over {svm.days} days Sense counted {fmtInt(svm.sense_kwh)} kWh; the meter you're billed on counted {fmtInt(svm.meter_kwh)} kWh.
        {Math.abs(pct) < 2 ? ' Your Sense readings can be trusted for the bill.' : ' Bill estimates use the meter where it has data.'}</p>
    </section>
  )
}
