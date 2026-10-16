import { useEffect, useMemo, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { SectionTitle, Empty } from '../../ui/primitives'
import { api, busyTyping } from '../../api/api'
import { fmtInt } from '../../ui/format'
import { compareSoFar, hasNext, monthGrid, nextDate, parse, periodLabel, shift, todayIso } from '../../ui/history'

// History: days, months and years of energy and cost from Sense's own cloud history
// (Senseless only has its own readings since it started). Click to drill down.

function useHistory(scale, date) {
  const [state, setState] = useState({ cur: null, prev: null, loading: true, error: null })
  useEffect(() => {
    let alive = true
    setState(s => ({ ...s, loading: true, error: null }))
    Promise.all([
      api('GET', `/api/history?scale=${scale}&date=${date}`),
      api('GET', `/api/history?scale=${scale}&date=${shift(date, scale, -1)}`).catch(() => null),
    ]).then(([cur, prev]) => { if (alive) setState({ cur, prev, loading: false, error: null }) })
      .catch(e => { if (alive) setState({ cur: null, prev: null, loading: false, error: e.message }) })
    return () => { alive = false }
  }, [scale, date])
  return state
}

const money = c => (c == null ? '—' : `$${(c / 100).toFixed(2)}`)
const kwhFmt = k => (k == null ? '—' : k >= 100 ? fmtInt(k) : k.toFixed(1))

function Stat({ value, unit, label, color = C.bright }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 30, fontWeight: 700, color, whiteSpace: 'nowrap' }}>{value}{unit && <span style={{ fontSize: 13, color: C.muted, fontWeight: 500 }}> {unit}</span>}</div>
      <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, color: C.dim, textTransform: 'uppercase' }}>{label}</div>
    </div>
  )
}

function Calendar({ date, data, onPick }) {
  const cells = monthGrid(date)
  const today = todayIso()
  const vals = data?.kwh ?? []
  const max = Math.max(1, ...vals.filter(v => v > 0))
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 6, marginBottom: 6 }}>
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(d => (
          <div key={d} style={{ fontFamily: MONO, fontSize: 10, color: C.dim, textAlign: 'center', letterSpacing: 1 }}>{d}</div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 6 }}>
        {cells.map((day, i) => {
          if (!day) return <div key={`b${i}`} />
          const n = parse(day).getDate(), v = vals[n - 1], future = day > today
          const t = v > 0 ? v / max : 0
          return (
            <button key={day} type="button" disabled={future} onClick={() => onPick(day)}
              aria-label={`${parse(day).toDateString()}: ${v > 0 ? v.toFixed(1) + ' kWh' : 'no data'}`}
              style={{
                minHeight: 58, borderRadius: 10, cursor: future ? 'default' : 'pointer', padding: '6px 7px', textAlign: 'left',
                display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
                background: v > 0 ? `linear-gradient(${alpha(C.accent, Math.round(8 + t * 52).toString(16).padStart(2, '0'))}, ${alpha(C.accent, Math.round(8 + t * 52).toString(16).padStart(2, '0'))}), ${C.field}` : C.field,
                border: `1px solid ${day === today ? alpha(C.accent, '90') : C.line}`, color: future ? C.faint : C.text, opacity: future ? 0.5 : 1,
              }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: future ? C.faint : C.muted }}>{n}</span>
              {v > 0 && <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700 }}>{v.toFixed(0)}<span style={{ fontWeight: 400, color: C.muted }}> kWh</span></span>}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function Bars({ values, labels, onPick, highlight }) {
  const max = Math.max(0.001, ...values.filter(v => v != null))
  return (
    <div>
      <div role="img" aria-label="Energy per period" style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 180 }}>
        {values.map((v, i) => (
          <button key={i} type="button" onClick={onPick ? () => onPick(i) : undefined} disabled={!onPick || !(v > 0)}
            title={`${labels[i]}: ${v != null ? v.toFixed(2) : '—'} kWh`} aria-label={`${labels[i]}: ${v != null ? v.toFixed(1) : 'no'} kWh`}
            style={{ flex: 1, minWidth: 0, height: v > 0 ? Math.max(4, (v / max) * 176) : 3, borderRadius: '4px 4px 1px 1px', border: 'none', padding: 0,
              cursor: onPick && v > 0 ? 'pointer' : 'default', background: i === highlight ? C.accent : v > max * 0.8 ? alpha(C.orange, 'cc') : alpha(C.text, '30') }} />
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 10, color: C.faint, marginTop: 6 }}>
        {labels.filter((_, i) => i % Math.ceil(labels.length / 6) === 0).map(l => <span key={l}>{l}</span>)}
      </div>
    </div>
  )
}

function Devices({ devices, total }) {
  if (!devices?.length) return <div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>No device breakdown for this period.</div>
  const max = devices[0].kwh
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {devices.slice(0, 14).map(d => (
        <div key={d.id ?? d.name} style={{ display: 'grid', gridTemplateColumns: 'minmax(110px, 1.2fr) 3fr auto', gap: 10, alignItems: 'center', fontFamily: MONO, fontSize: 12 }}>
          <span style={{ color: C.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.name}</span>
          <div style={{ height: 8, borderRadius: 4, background: C.field }}>
            <div style={{ height: 8, borderRadius: 4, width: `${Math.max(1, (d.kwh / max) * 100)}%`, background: d.name === 'Always On' ? C.amber : d.name === 'Other' ? alpha(C.text, '40') : C.accent }} />
          </div>
          <span style={{ color: C.muted, whiteSpace: 'nowrap' }}>{kwhFmt(d.kwh)} kWh · {total ? Math.round((d.kwh / total) * 100) : 0}%</span>
        </div>
      ))}
    </div>
  )
}

export function HistoryView() {
  const { sp } = useTheme()
  const [scale, setScale] = useState('month')
  const [date, setDate] = useState(todayIso)
  const { cur, prev, loading, error } = useHistory(scale, date)
  const cmp = useMemo(() => compareSoFar(cur, prev), [cur, prev])
  const atNow = !hasNext(date, scale, todayIso())

  useEffect(() => {
    const onKey = e => {
      if (busyTyping() || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'ArrowLeft') setDate(d => shift(d, scale, -1))
      else if (e.key === 'ArrowRight' && !atNow) setDate(d => nextDate(d, scale, todayIso()))
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [scale, atNow])

  const chip = on => ({ cursor: 'pointer', fontFamily: MONO, fontSize: 11, letterSpacing: 1, padding: '7px 12px', borderRadius: 7, textTransform: 'uppercase',
    color: on ? C.accent : C.muted, background: on ? alpha(C.accent, '14') : 'transparent', border: `1px solid ${on ? alpha(C.accent, '50') : 'transparent'}` })
  const elapsed = cur?.kwh?.filter(v => v > 0).length ?? 0
  const unit = scale === 'year' ? 'month' : scale === 'month' ? 'day' : 'hour'
  const d0 = parse(date)
  const labels = scale === 'day' ? Array.from({ length: 24 }, (_, i) => `${i}:00`)
    : scale === 'year' ? ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] : []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: sp(16) }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
        <h1 style={{ fontSize: 26, fontWeight: 700 }}>History</h1>
        <span style={{ fontSize: 18, color: C.body }}>{periodLabel(date, scale)}</span>
        {loading && <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading…</span>}
        <span style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 4, padding: 4, border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel }}>
          <button type="button" onClick={() => setDate(d => shift(d, scale, -1))} aria-label="Previous (←)" style={chip(false)}>←</button>
          <button type="button" onClick={() => setDate(d => nextDate(d, scale, todayIso()))} aria-label="Next (→)" disabled={atNow} style={{ ...chip(false), opacity: atNow ? 0.4 : 1 }}>→</button>
          <button type="button" onClick={() => setDate(todayIso())} style={chip(false)}>Today</button>
        </div>
        <div role="group" aria-label="Scale" style={{ display: 'flex', gap: 4, padding: 4, border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel }}>
          {['day', 'month', 'year'].map(s => <button key={s} type="button" aria-pressed={scale === s} onClick={() => setScale(s)} style={chip(scale === s)}>{s}</button>)}
        </div>
      </div>

      {error && <Empty title="Couldn't load history">{error}</Empty>}
      {cur && (
        <>
          <section style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: 22, display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 16 }}>
            <Stat value={kwhFmt(cur.kwh_total)} unit="kWh" label="Energy" />
            <Stat value={money(cur.cost_total_cents)} label="Cost" />
            {scale !== 'day' && <Stat value={elapsed ? kwhFmt(cur.kwh_total / elapsed) : '—'} unit="kWh" label={`Average per ${unit}`} />}
            {cmp && <Stat value={`${cmp.pct > 0 ? '+' : ''}${cmp.pct}%`} label={`vs previous ${scale} (first ${cmp.buckets} ${unit}${cmp.buckets > 1 ? 's' : ''})`}
              color={cmp.pct > 5 ? C.orange : cmp.pct < -5 ? C.green : C.bright} />}
          </section>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: sp(16), alignItems: 'flex-start' }}>
            <section style={{ flex: '999 1 560px', minWidth: 0, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <SectionTitle style={{ margin: 0 }}>{scale === 'month' ? 'Each day' : scale === 'day' ? 'Each hour' : 'Each month'}</SectionTitle>
              {scale === 'month' && <Calendar date={date} data={cur} onPick={d => { setDate(d); setScale('day') }} />}
              {scale === 'day' && <Bars values={cur.kwh} labels={labels} highlight={date === todayIso() ? new Date().getHours() : -1} />}
              {scale === 'year' && <Bars values={cur.kwh} labels={labels} highlight={d0.getFullYear() === new Date().getFullYear() ? new Date().getMonth() : -1}
                onPick={i => { setDate(`${d0.getFullYear()}-${String(i + 1).padStart(2, '0')}-01`); setScale('month') }} />}
            </section>
            <section style={{ flex: '1 1 360px', minWidth: 0, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <SectionTitle style={{ margin: 0 }} right={<span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>Sense's attribution</span>}>Where it went</SectionTitle>
              <Devices devices={cur.devices} total={cur.kwh_total} />
            </section>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
            From Sense's cloud history{cur.stale ? ' (Sense unreachable: showing the last copy)' : ''}. Senseless's own detection covers the last 48 h on Loads and Timeline.
          </div>
        </>
      )}
    </div>
  )
}
