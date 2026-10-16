import { useCallback, useEffect, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { toast } from '../../ui/primitives'
import { api } from '../../api/api'
import { fmtInt } from '../../ui/format'
import { breakdown, cycleBars, describeComponents, touAdvice, usd } from '../../ui/billing'
import { BillImport } from './BillImport'
import { BillInsights } from './BillInsights'

// Bill: this billing cycle priced the way the utility bills it (fixed charge, delivery, supply, tax),
// projected to the end of the cycle, compared across rate plans, and checked against bills you enter.

const PART_COLOR = { fixed: () => C.muted, delivery: () => C.l1, supply: () => C.accent, tax: () => C.orange }

function useBilling() {
  const [state, setState] = useState({ config: null, cycle: null, bills: [], insights: null, error: null, loading: true })
  const load = useCallback(async () => {
    try {
      const [config, bills] = await Promise.all([api('GET', '/api/billing'), api('GET', '/api/billing/bills')])
      const pack = await api('GET', `/api/billing/tariffs/${config.settings.pack}`).catch(() => null)
      setState(s => ({ ...s, config, bills, pack, loading: false }))
      const [cycle, insights] = await Promise.all([api('GET', '/api/billing/cycle'), api('GET', '/api/billing/insights').catch(() => null)])
      setState(s => ({ ...s, cycle, insights, error: null }))
    } catch (e) {
      setState(s => ({ ...s, error: e.message, loading: false }))
    }
  }, [])
  useEffect(() => { load() }, [load])
  const save = async patch => {
    try { await api('POST', '/api/billing', patch); await load() } catch (e) { toast(e.message) }
  }
  const addBill = async b => { await api('POST', '/api/billing/bills', b); await load() }
  const removeBill = async id => { await api('DELETE', `/api/billing/bills/${id}`); await load() }
  return { ...state, save, addBill, removeBill, reload: load }
}

const card = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: 18, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }
const kicker = { fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color: C.accent }
const small = { fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.6 }
const fmtDate = iso => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

export function BillView() {
  const { sp, isMobile } = useTheme()
  const b = useBilling()
  const c = b.cycle
  const proj = c?.projected
  const shown = proj?.calibrated_total ?? proj?.total
  const tou = touAdvice(c)
  const senseProjected = c?.sense_cost_so_far && c.kwh_so_far ? (c.sense_cost_so_far / c.kwh_so_far) * c.kwh_projected : null

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: sp(16) }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 26, fontWeight: 700 }}>Bill</h1>
        {c && <span style={{ fontFamily: MONO, fontSize: 12, color: C.muted }}>{fmtDate(c.start)} → {fmtDate(c.end)} · day {Math.floor(c.elapsed_days) + 1} of {c.days}</span>}
      </div>
      {b.error && <div role="alert" style={{ ...card, color: C.amber, fontFamily: MONO, fontSize: 12 }}>{b.error}</div>}
      {!c && !b.error && <div style={{ ...card, color: C.muted }}>Working out this cycle from Sense's history…</div>}

      {c && (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0, 1.1fr) minmax(0, 1fr)', gap: sp(14) }}>
          <section style={card} aria-label="Projected bill">
            <div style={kicker}>Projected bill</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 56, fontWeight: 700, color: C.bright, letterSpacing: -1.5, fontVariantNumeric: 'tabular-nums' }}>{usd(shown)}</span>
              <span style={{ fontFamily: MONO, fontSize: 12, color: C.muted }}>{fmtInt(c.kwh_projected)} kWh · {proj?.effective_cents_per_kwh}¢/kWh all-in</span>
            </div>
            {c.missing_days?.length > 0 && <div style={small}>Sense has no total for {c.missing_days.length} day{c.missing_days.length > 1 ? 's' : ''} this cycle; counted at the recent daily rate.</div>}
            {proj?.calibrated_total != null && <div style={small}>Adjusted ×{c.calibration} to match your entered bills (unadjusted {usd(proj.total)}).</div>}
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontFamily: MONO, fontSize: 12, color: C.body }}>
              <span>So far <b style={{ color: C.bright }}>{usd(c.so_far?.total)}</b> · {fmtInt(c.kwh_so_far)} kWh</span>
              <span>{c.kwh_per_day} kWh/day lately</span>
              <span>Each extra kWh ≈ <b style={{ color: C.bright }}>{proj?.marginal_cents_per_kwh}¢</b></span>
            </div>
            <Breakdown bill={proj} />
            {senseProjected != null && (
              <p style={{ ...small, color: C.body }}>
                Sense's flat-rate estimate for this cycle is about <b>{usd(senseProjected)}</b>: it prices every kWh at one rate and leaves out the fixed charge, most of delivery and tax.
              </p>
            )}
          </section>
          <section style={card} aria-label="Daily use this cycle">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={kicker}>Each day</div>
              <span style={small}>kWh · faded = projected</span>
            </div>
            <DailyBars cycle={c} />
          </section>
        </div>
      )}

      {c && !b.insights?.plans_12mo && (
        <section style={card} aria-label="Rate plans">
          <div style={kicker}>Your usage on each rate plan</div>
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : `repeat(${c.compare.length}, minmax(0, 1fr))`, gap: 10 }}>
            {c.compare.map(p => {
              const current = p.plan === b.config?.settings.plan
              const better = p.delta < -0.5
              return (
                <div key={p.plan} style={{ border: `1px solid ${current ? alpha(C.accent, '70') : better ? alpha(C.green, '60') : C.line2}`, borderRadius: 12, padding: 14,
                  background: current ? alpha(C.accent, '0c') : 'transparent', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: C.bright }}>{p.label}</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: C.bright, fontVariantNumeric: 'tabular-nums' }}>{usd(p.total)}</div>
                  <div style={{ fontFamily: MONO, fontSize: 11, color: current ? C.accent : better ? C.green : C.muted }}>
                    {current ? 'your plan' : `${p.delta < 0 ? 'saves' : 'costs'} ${usd(Math.abs(p.delta))} ${p.delta < 0 ? '' : 'more'}`}
                  </div>
                  {!current && (
                    <button type="button" onClick={() => b.save({ plan: p.plan })}
                      style={{ alignSelf: 'flex-start', marginTop: 4, minHeight: 32, padding: '0 10px', borderRadius: 8, cursor: 'pointer', fontFamily: MONO, fontSize: 11,
                        color: C.muted, background: 'transparent', border: `1px solid ${C.line2}` }}>I'm on this plan</button>
                  )}
                </div>
              )
            })}
          </div>
          {tou && <p style={{ color: C.body, fontSize: 14, lineHeight: 1.5 }}>{tou.text} Measured over the last {c.on_peak_days} days; switching may need a meter exchange, and supply is priced the same either way.</p>}
        </section>
      )}

      <BillInsights insights={b.insights} currentPlan={b.config?.settings.plan} labelOf={id => b.pack?.plans.find(p => p.id === id)?.label ?? id} />

      <section style={card} aria-label="Import bills and usage">
        <div style={kicker}>Import</div>
        <BillImport onImported={b.reload} />
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0, 1fr) minmax(0, 1fr)', gap: sp(14) }}>
        <Bills b={b} />
        {b.config && <Settings config={b.config} save={b.save} />}
      </div>
    </div>
  )
}

function Breakdown({ bill }) {
  const parts = breakdown(bill)
  if (!parts.length) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div role="img" aria-label={parts.map(p => `${p.label} ${usd(p.value)}`).join(', ')}
        style={{ display: 'flex', height: 14, borderRadius: 7, overflow: 'hidden', background: C.field }}>
        {parts.map(p => <div key={p.key} style={{ width: `${p.pct}%`, minWidth: 3, background: PART_COLOR[p.key]() }} />)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
        {parts.map(p => (
          <div key={p.key} style={{ display: 'flex', gap: 8, alignItems: 'baseline', minWidth: 0 }}>
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 2, background: PART_COLOR[p.key](), flexShrink: 0 }} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted, whiteSpace: 'nowrap' }}>{p.label}</span>
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.bright, marginLeft: 'auto' }}>{usd(p.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function DailyBars({ cycle }) {
  const bars = cycleBars(cycle)
  const max = Math.max(1, ...bars.map(x => x.kwh ?? 0))
  return (
    <div>
      <div role="img" aria-label={`Daily kWh this cycle, about ${cycle.kwh_per_day} per day`} style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 150 }}>
        {bars.map(x => (
          <div key={x.date} title={`${fmtDate(x.date)}: ${x.kwh?.toFixed(1)} kWh${x.kind === 'projected' ? ' (projected)' : ''}`}
            style={{ flex: 1, minWidth: 0, height: `${((x.kwh ?? 0) / max) * 100}%`, borderRadius: '3px 3px 0 0',
              background: x.kind === 'projected' ? alpha(C.accent, '26') : x.kind === 'today' ? C.amber : alpha(C.accent, 'c0'),
              border: x.kind === 'projected' ? `1px dashed ${alpha(C.accent, '55')}` : 'none', borderBottom: 'none' }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 6 }}>
        <span>{fmtDate(cycle.start)}</span><span>{fmtDate(cycle.end)}</span>
      </div>
    </div>
  )
}

function Bills({ b }) {
  const [form, setForm] = useState({ start: '', end: '', kwh: '', amount: '', on: '' })
  const tou = !!b.pack?.plans.find(p => p.id === b.config?.settings.plan)?.components?.some(c => c.type === 'tou')
  const submit = async e => {
    e.preventDefault()
    try {
      await b.addBill({ start: form.start, end: form.end, kwh: Number(form.kwh), amount: Number(form.amount),
        ...(form.on.trim() !== '' ? { on_peak_kwh: Number(form.on) } : {}) })
      setForm({ start: '', end: '', kwh: '', amount: '', on: '' })
      toast('Bill added')
    } catch (err) { toast(err.message) }
  }
  const set = k => e => setForm(f => ({ ...f, [k]: e.target.value }))
  return (
    <section style={card} aria-label="Your bills">
      <div style={kicker}>Your bills</div>
      <p style={small}>Enter a few real bills to see how close the estimate is. With "match my bills" on, projections are scaled by the average difference.</p>
      {b.bills.map(x => ({ ...x, own: b.insights?.bills?.find(i => i.start === x.start && i.end === x.end)?.check })).map(x => (
        <div key={x.id} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 10, alignItems: 'center', fontFamily: MONO, fontSize: 12,
          borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
          <span style={{ color: C.body }}>{fmtDate(x.start)} → {fmtDate(x.end)} · {fmtInt(x.kwh)} kWh</span>
          <span style={{ color: C.bright }}>{usd(x.amount)} {x.own
            ? <span style={{ color: Math.abs(x.own.diff) <= 0.05 ? C.green : C.amber }} title="Recomputed from the rates printed on this bill">(model {usd(x.own.model_total)}{Math.abs(x.own.diff) <= 0.05 ? ' ✓' : `, ${x.own.diff >= 0 ? '+' : ''}${usd(x.own.diff)}`})</span>
            : x.estimate == null
              ? <span style={{ color: C.dim }}>(add its on-peak kWh to compare)</span>
              : <span style={{ color: Math.abs(x.ratio - 1) < 0.05 ? C.green : C.amber }} title="Estimated with today's tariff">(est. {usd(x.estimate)}, {x.diff >= 0 ? '+' : ''}{usd(x.diff)})</span>}</span>
          <button type="button" aria-label={`Remove bill ${x.start}`} onClick={() => b.removeBill(x.id).catch(e => toast(e.message))}
            style={{ width: 32, height: 32, borderRadius: 8, cursor: 'pointer', background: 'transparent', color: C.dim, border: `1px solid ${C.line2}` }}>×</button>
        </div>
      ))}
      <form onSubmit={submit} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8, alignItems: 'end' }}>
        <Field label="From"><input type="date" required value={form.start} onChange={set('start')} style={input} /></Field>
        <Field label="To"><input type="date" required value={form.end} onChange={set('end')} style={input} /></Field>
        <Field label="kWh"><input type="number" required min="1" step="any" inputMode="decimal" value={form.kwh} onChange={set('kwh')} style={input} /></Field>
        <Field label="Total $"><input type="number" required min="0.01" step="0.01" inputMode="decimal" value={form.amount} onChange={set('amount')} style={input} /></Field>
        {tou && <Field label="On-peak kWh"><input type="number" min="0" step="any" inputMode="decimal" value={form.on} onChange={set('on')} style={input} /></Field>}
        <button type="submit" style={{ ...input, cursor: 'pointer', color: C.bg, background: C.accent, border: `1px solid ${C.accent}`, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, fontSize: 12 }}>Add bill</button>
      </form>
    </section>
  )
}

function Settings({ config, save }) {
  const s = config.settings
  const [packs, setPacks] = useState([])
  const [pack, setPack] = useState(null)
  useEffect(() => { api('GET', '/api/billing/tariffs').then(setPacks).catch(() => {}) }, [])
  useEffect(() => { if (s.pack) api('GET', `/api/billing/tariffs/${s.pack}`).then(setPack).catch(() => setPack(null)) }, [s.pack])
  const plan = pack?.plans.find(p => p.id === s.plan)
  const supply = pack?.supply?.find(p => p.id === s.supply)
  const [draft, setDraft] = useState(null)
  const num = (k, scale = 1) => (draft?.[k] ?? String(Number((s[k] * scale).toFixed(6))))   // 0.12721*100 is 12.720999…
  const commit = (k, scale = 1) => {
    if (draft?.[k] == null) return
    const raw = String(draft[k]).trim()
    setDraft(d => ({ ...d, [k]: null }))
    if (raw === '') return            // a cleared field means "not decided yet", never zero
    const v = Number(raw) / scale
    if (Number.isFinite(v) && v !== s[k]) save({ [k]: k === 'billing_day' || k === 'tax_exempt_kwh' ? Math.round(v) : v })
  }
  const edit = k => e => setDraft(d => ({ ...d, [k]: e.target.value }))
  const choosePack = async id => {
    const p = await api('GET', `/api/billing/tariffs/${id}`).catch(() => null)
    if (!p) return
    save({ pack: id, plan: p.plans[0]?.id, supply: p.supply?.[0]?.id ?? null })
  }
  return (
    <section style={card} aria-label="Tariff settings">
      <div style={kicker}>Tariff</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
        <Field label="Utility">
          <select value={s.pack} onChange={e => choosePack(e.target.value)} style={input}>
            {packs.map(p => <option key={p.id} value={p.id}>{p.utility}{p.region ? ` (${p.region})` : ''}</option>)}
          </select>
        </Field>
        <Field label="Delivery plan">
          <select value={s.plan} onChange={e => save({ plan: e.target.value })} style={input} disabled={!pack}>
            {(pack?.plans ?? []).map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </Field>
        {(pack?.supply?.length ?? 0) > 0 && (
          <Field label="Supply">
            <select value={s.supply ?? ''} onChange={e => save({ supply: e.target.value || null })} style={input}>
              {pack.supply.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </Field>
        )}
        <Field label="Cycle starts on day">
          <input type="number" min="1" max="31" inputMode="numeric" value={num('billing_day')} onChange={edit('billing_day')} onBlur={() => commit('billing_day')} style={input} />
        </Field>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontFamily: MONO, fontSize: 12, color: C.body, cursor: 'pointer' }}>
          <input type="checkbox" checked={s.calibrate} onChange={e => save({ calibrate: e.target.checked })} style={{ width: 18, height: 18, accentColor: C.accent }} />
          Match my bills
        </label>
      </div>
      {s.plan === 'custom' && <CustomPlan s={s} save={save} />}
      {plan && (
        <div style={small}>
          {describeComponents(plan.components)}{supply ? ` Supply: ${describeComponents(supply.components)}` : ''}
          {(pack?.taxes ?? []).map(t => ` ${t.label ?? 'Tax'} ${(t.rate * 100).toFixed(1)}%${t.exempt_kwh ? ` above ${fmtInt(t.exempt_kwh)} kWh/month` : ''}.`).join('')}
          <br />Source: {plan.source}{plan.effective ? `, effective ${plan.effective}` : ''}{plan.expires ? `; expires ${plan.expires}` : ''}.
          {supply ? ` Supply: ${supply.source}.` : ''} Your cycle start is the meter-read date on your bill.
        </div>
      )}
      <div style={small}>Another utility? Its plans are a small data file (a tariff pack): see notes/energy-providers.md, or POST one to /api/billing/tariffs.</div>
    </section>
  )
}

function CustomPlan({ s, save }) {
  const [f, setF] = useState({ fixed: s.custom.fixed, fixed_kwh: s.custom.fixed_kwh, delivery: s.custom.delivery * 100 })
  const commit = () => {
    if ([f.fixed, f.fixed_kwh, f.delivery].some(v => String(v).trim() === '' || !Number.isFinite(Number(v)))) {
      setF({ fixed: s.custom.fixed, fixed_kwh: s.custom.fixed_kwh, delivery: s.custom.delivery * 100 })   // back to what's saved
      return
    }
    save({ custom: { fixed: Number(f.fixed), fixed_kwh: Math.round(Number(f.fixed_kwh)), delivery: Number(f.delivery) / 100 } })
  }
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10 }}>
      <Field label="Fixed $/month"><input type="number" step="0.01" value={f.fixed} onChange={e => setF({ ...f, fixed: e.target.value })} onBlur={commit} style={input} /></Field>
      <Field label="kWh it includes"><input type="number" value={f.fixed_kwh} onChange={e => setF({ ...f, fixed_kwh: e.target.value })} onBlur={commit} style={input} /></Field>
      <Field label="Delivery ¢/kWh"><input type="number" step="0.0001" value={f.delivery} onChange={e => setF({ ...f, delivery: e.target.value })} onBlur={commit} style={input} /></Field>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.dim, minWidth: 0 }}>
      {label}
      {children}
    </label>
  )
}

const input = { height: 40, background: C.field, border: `1px solid ${C.line2}`, borderRadius: 8, color: C.text, fontFamily: MONO, fontSize: 14,
  padding: '0 10px', minWidth: 0, width: '100%', textTransform: 'none', letterSpacing: 0 }
