import { useCallback, useEffect, useRef, useState } from 'react'
import { C, MONO, alpha, glow, useTheme } from '../../ui/theme'
import { toast } from '../../ui/primitives'
import { api } from '../../api/api'
import { fmtInt, fmtSigned } from '../../ui/format'
import { explained, huntDrop, legAverage } from '../../ui/hunt'

// Breaker Hunt, for standing at the panel with a phone: capture a baseline, flip a breaker,
// see exactly what dropped on which leg, name the breaker, flip it back, repeat.

function useReadings(latest) {
  const buf = useRef([])
  const [, tick] = useState(0)
  useEffect(() => {
    const [l1, l2] = latest?.channels ?? []
    // only server-timestamped live readings: the snapshot has no t, and the phone's clock may differ
    if (l1 == null || latest.t == null) return
    const t = latest.t
    buf.current = buf.current.filter(r => r.t >= t - 60).concat({ t, l1, l2 })
    tick(n => n + 1)
  }, [latest])
  return buf.current
}

function useCircuits() {
  const [list, setList] = useState([])
  const load = useCallback(() => api('GET', '/api/circuits').then(setList).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  const add = async c => { await api('POST', '/api/circuits', c); await load() }
  const remove = async id => { await api('DELETE', `/api/circuits/${id}`); await load() }
  return { list, add, remove }
}

function Leg({ leg, w, drop, active }) {
  const c = leg === 1 ? C.l1 : C.l2
  return (
    <div style={{ background: C.panel, border: `1px solid ${alpha(c, active ? '80' : '40')}`, borderRadius: 16, padding: '14px 16px',
      boxShadow: active ? `0 0 22px ${glow(c, '30')}` : 'none', transition: 'box-shadow .3s, border-color .3s', minWidth: 0 }}>
      <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 2, color: c }}>L{leg}</div>
      <div style={{ fontSize: 40, fontWeight: 700, color: C.bright, fontVariantNumeric: 'tabular-nums' }}>
        {fmtInt(w)}<span style={{ fontSize: 14, color: C.muted, fontWeight: 500 }}> W</span>
      </div>
      {drop != null && <div style={{ fontFamily: MONO, fontSize: 14, color: Math.abs(drop) >= 15 ? C.accent : C.dim }}>{Math.abs(drop) >= 15 ? fmtSigned(drop) : 'no change'}</div>}
    </div>
  )
}

const bigButton = (primary) => ({
  minHeight: 52, padding: '0 20px', borderRadius: 12, cursor: 'pointer', fontFamily: MONO, fontSize: 13, letterSpacing: 1,
  textTransform: 'uppercase', color: primary ? C.bg : C.accent, background: primary ? C.accent : alpha(C.accent, '14'),
  border: `1px solid ${alpha(C.accent, primary ? 'ff' : '50')}`, fontWeight: 700,
})

export function HuntView({ live }) {
  const { sp } = useTheme()
  const readings = useReadings(live.latest)
  const circuits = useCircuits()
  const [stage, setStage] = useState('idle')        // idle | settling | watching | flipback
  const [baseline, setBaseline] = useState(null)
  const [breaker, setBreaker] = useState('')
  const [name, setName] = useState('')
  const [lastSaved, setLastSaved] = useState(null)
  const now = live.latest?.t ?? Date.now() / 1000
  const current = legAverage(readings, 3, now)
  const [l1, l2] = live.latest?.channels ?? []
  const drop = baseline ? huntDrop(baseline, current, live.deltas) : null
  const alwaysOn = live.latest?.devices?.find(d => d.name === 'Always On')?.w
  const exp = explained(circuits.list, alwaysOn)

  const healthy = live.status.state === 'live' && live.status.healthy
  const settleTimer = useRef(null)
  useEffect(() => () => clearTimeout(settleTimer.current), [])
  const latestRef = useRef({ readings, now })
  latestRef.current = { readings, now }
  // Start / Next: let the panel settle (a breaker just switched back on), then average 3 fresh seconds
  const start = () => {
    if (!healthy) { toast('The live feed is stale; wait for it to reconnect'); return }
    setStage('settling')
    clearTimeout(settleTimer.current)
    settleTimer.current = setTimeout(() => {
      const { readings: rs, now: n } = latestRef.current
      const b = legAverage(rs, 3, n)
      if (b.n < 3) { toast('Waiting for live readings…'); setStage('idle'); return }
      setBaseline({ t: n, l1: b.l1, l2: b.l2 })
      setStage('watching')
    }, 4000)
  }
  const save = async e => {
    e.preventDefault()
    if (!breaker.trim()) { toast('Which breaker? e.g. 14'); return }
    try {
      await circuits.add({ breaker: breaker.trim(), name: name.trim() || null, l1_w: Math.round(drop.l1), l2_w: Math.round(drop.l2) })
      setLastSaved(breaker.trim())
      setBreaker(''); setName(''); setStage('flipback')
      toast(`Breaker ${breaker.trim()} saved`)
    } catch (err) { toast(`Couldn't save: ${err.message}`) }
  }

  return (
    <div style={{ maxWidth: 560, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: sp(14) }}>
      <div>
        <h1 style={{ fontSize: 24, fontWeight: 700 }}>Breaker hunt</h1>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>Flip breakers one at a time and see exactly what each one feeds.</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
        <Leg leg={1} w={l1} drop={drop?.l1} active={drop && Math.abs(drop.l1) >= 15} />
        <Leg leg={2} w={l2} drop={drop?.l2} active={drop && Math.abs(drop.l2) >= 15} />
      </div>

      {!healthy && stage !== 'idle' && (
        <div role="alert" style={{ fontFamily: MONO, fontSize: 12, color: C.amber, padding: '10px 12px', borderRadius: 10, border: `1px solid ${alpha(C.amber, '50')}` }}>
          Live feed stale: readings may be old. Wait for "Live", then re-baseline.
        </div>
      )}
      {stage === 'settling' && (
        <section aria-live="polite" style={{ background: C.panel, border: `1px dashed ${C.line2}`, borderRadius: 16, padding: 20, textAlign: 'center', color: C.body }}>
          Measuring the baseline… hold still for a few seconds.
        </section>
      )}
      {stage === 'idle' && (
        <section style={{ background: C.panel, border: `1px dashed ${C.line2}`, borderRadius: 16, padding: 20, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center', textAlign: 'center' }}>
          <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>Stand at the panel. Tap start, then switch off one breaker.</p>
          <button type="button" onClick={start} disabled={!healthy} style={{ ...bigButton(true), opacity: healthy ? 1 : 0.45 }}>Start</button>
          {!healthy && <div style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>Waiting for the live feed…</div>}
        </section>
      )}

      {stage === 'watching' && drop && (
        <section aria-live="polite" style={{ background: C.panel, border: `1px solid ${alpha(C.accent, drop.meaningful ? '70' : '30')}`, borderRadius: 16, padding: 18,
          display: 'flex', flexDirection: 'column', gap: 12, boxShadow: drop.meaningful ? `0 0 26px ${glow(C.accent, '22')}` : 'none' }}>
          {drop.meaningful && drop.total > 0 ? (
            <>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color: C.amber }}>Something turned on</div>
              <div style={{ fontSize: 40, fontWeight: 700, color: C.amber }}>{fmtSigned(drop.total)}</div>
              <p style={{ color: C.body, fontSize: 14, lineHeight: 1.5 }}>
                Load went up, so this wasn't a breaker switching off (an appliance started). Re-baseline, then flip the breaker.
              </p>
              <button type="button" onClick={start} style={bigButton(true)}>Re-baseline</button>
            </>
          ) : !drop.meaningful ? (
            <>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color: C.muted }}>Listening…</div>
              <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>
                Baseline L1 {fmtInt(baseline.l1)} W · L2 {fmtInt(baseline.l2)} W. Switch off the next breaker; the drop shows here within a second.
              </p>
              <button type="button" onClick={start} style={{ ...bigButton(false), minHeight: 44, alignSelf: 'flex-start' }}>Re-baseline</button>
            </>
          ) : (
            <>
              <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color: C.accent }}>
                {drop.both ? '240 V breaker' : `Dropped on L${Math.abs(drop.l1) > Math.abs(drop.l2) ? 1 : 2}`}
                {drop.steps.count ? ` · ${drop.steps.count} step${drop.steps.count > 1 ? 's' : ''} caught` : ''}
              </div>
              <div style={{ fontSize: 48, fontWeight: 700, color: C.accent, letterSpacing: -1 }}>{fmtSigned(drop.total)}</div>
              {alwaysOn && drop.total < 0 && (
                <p style={{ color: C.body, fontSize: 14 }}>That's <b style={{ color: C.bright }}>{Math.min(100, Math.round((-drop.total / alwaysOn) * 100))}%</b> of your {fmtInt(alwaysOn)} W always-on load.</p>
              )}
              <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 8 }}>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.dim }}>
                    Breaker
                    <input value={breaker} onChange={e => setBreaker(e.target.value)} inputMode="numeric" placeholder="14" maxLength={20} style={inputStyle()} />
                  </label>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.dim }}>
                    What's on it
                    <input value={name} onChange={e => setName(e.target.value)} placeholder="Basement" maxLength={60} style={inputStyle()} />
                  </label>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button type="submit" style={bigButton(true)}>Save</button>
                  <button type="button" onClick={start} style={bigButton(false)}>Re-baseline</button>
                </div>
              </form>
            </>
          )}
        </section>
      )}

      {stage === 'flipback' && (
        <section style={{ background: C.panel, border: `1px solid ${alpha(C.green, '50')}`, borderRadius: 16, padding: 20, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center', textAlign: 'center' }}>
          <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>Switch breaker <b style={{ color: C.bright }}>{lastSaved}</b> back on, then tap next.</p>
          <button type="button" onClick={start} style={bigButton(true)}>Next breaker</button>
          <button type="button" onClick={() => setStage('idle')} style={{ ...bigButton(false), minHeight: 44 }}>Done for now</button>
        </section>
      )}

      <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 11, color: C.muted }}>
          <span>Mapped {circuits.list.length} breaker{circuits.list.length === 1 ? '' : 's'}</span>
          {exp.pct != null && <span>{fmtInt(exp.mapped)} / {fmtInt(alwaysOn)} W of always-on</span>}
        </div>
        {exp.pct != null && (
          <div role="progressbar" aria-valuenow={exp.pct} aria-valuemin={0} aria-valuemax={100} aria-label="Always-on load explained"
            style={{ height: 8, borderRadius: 4, background: C.field, overflow: 'hidden' }}>
            <div style={{ height: 8, width: `${exp.pct}%`, background: C.green, transition: 'width .4s' }} />
          </div>
        )}
        {circuits.list.slice().reverse().map(c => (
          <div key={c.id} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 10, alignItems: 'center', background: C.panel,
            border: `1px solid ${C.line}`, borderRadius: 12, padding: '10px 12px' }}>
            <span style={{ minWidth: 0 }}><b>#{c.breaker}</b>{c.name ? <span style={{ color: C.muted }}> · {c.name}</span> : null}</span>
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.accent }}>
              {Math.abs(c.l1_w) >= 15 && <span style={{ color: C.l1 }}>L1 {fmtSigned(c.l1_w)} </span>}
              {Math.abs(c.l2_w) >= 15 && <span style={{ color: C.l2 }}>L2 {fmtSigned(c.l2_w)}</span>}
            </span>
            <button type="button" aria-label={`Delete breaker ${c.breaker}`} onClick={() => circuits.remove(c.id).then(() => toast('Deleted'))}
              style={{ width: 36, height: 36, borderRadius: 8, cursor: 'pointer', background: 'transparent', color: C.dim, border: `1px solid ${C.line2}` }}>×</button>
          </div>
        ))}
      </section>
    </div>
  )
}

function inputStyle() {
  return { height: 48, background: C.field, border: `1px solid ${C.line2}`, borderRadius: 10, color: C.text, fontFamily: MONO,
    fontSize: 16, padding: '0 12px', outline: 'none', textTransform: 'none', letterSpacing: 0 }
}
