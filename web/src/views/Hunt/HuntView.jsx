import { useCallback, useEffect, useRef, useState } from 'react'
import { C, MONO, alpha, glow, useTheme } from '../../ui/theme'
import { toast } from '../../ui/primitives'
import { api } from '../../api/api'
import { fmtInt, fmtSigned } from '../../ui/format'
import { explained, fmtSecs, newerHunt } from '../../ui/hunt'
import { useBaseline } from '../../hooks/useLive'

// Hunt, for walking the house with a phone. Breakers: flip one at a time and see what each feeds.
// Devices: switch a device on and off and Senseless measures it, then you name it.
// The session lives on the server (/api/hunt), so a voice assistant driving the API and this screen
// stay in step: the prompt shown here is the one the assistant reads aloud.

function useHunt(pushed) {
  const [fetched, setFetched] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api('GET', '/api/hunt').then(setFetched).catch(() => {}) }, [])
  const hunt = newerHunt(fetched, pushed)
  const send = useCallback(async (cmd, body) => {
    setBusy(true)
    try {
      const r = await api('POST', `/api/hunt/${cmd}`, body ?? {})
      setFetched(r.hunt ?? r)
      return r
    } catch (e) {
      if (e.data?.hunt) setFetched(e.data.hunt)
      toast(e.message)
      throw e
    } finally {
      setBusy(false)
    }
  }, [])
  return { hunt, send, busy }
}

function useCircuits(version) {
  const [list, setList] = useState([])
  const load = useCallback(() => api('GET', '/api/circuits').then(setList).catch(() => {}), [])
  useEffect(() => { load() }, [load, version])
  const remove = async id => { await api('DELETE', `/api/circuits/${id}`); await load() }
  return { list, remove }
}

function useRooms() {
  const [rooms, setRooms] = useState([])
  useEffect(() => {
    api('GET', '/api/floors').then(fs => setRooms(fs.flatMap(f => f.rooms.map(r => ({ id: r.id, name: fs.length > 1 ? `${r.name} · ${f.name}` : r.name })))))
      .catch(() => {})
  }, [])
  return rooms
}

function Leg({ leg, w, change, active }) {
  const c = leg === 1 ? C.l1 : C.l2
  return (
    <div style={{ background: C.panel, border: `1px solid ${alpha(c, active ? '80' : '40')}`, borderRadius: 16, padding: '14px 16px',
      boxShadow: active ? `0 0 22px ${glow(c, '30')}` : 'none', transition: 'box-shadow .3s, border-color .3s', minWidth: 0 }}>
      <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 2, color: c }}>L{leg}</div>
      <div style={{ fontSize: 40, fontWeight: 700, color: C.bright, fontVariantNumeric: 'tabular-nums' }}>
        {fmtInt(w)}<span style={{ fontSize: 14, color: C.muted, fontWeight: 500 }}> W</span>
      </div>
      {change != null && <div style={{ fontFamily: MONO, fontSize: 14, color: Math.abs(change) >= 15 ? C.accent : C.dim }}>{Math.abs(change) >= 15 ? fmtSigned(change) : 'no change'}</div>}
    </div>
  )
}

const bigButton = (primary, color = C.accent) => ({
  minHeight: 52, padding: '0 20px', borderRadius: 12, cursor: 'pointer', fontFamily: MONO, fontSize: 13, letterSpacing: 1,
  textTransform: 'uppercase', color: primary ? C.bg : color, background: primary ? color : alpha(color, '14'),
  border: `1px solid ${alpha(color, primary ? 'ff' : '50')}`, fontWeight: 700,
})

const card = (color = C.line2, dashed = false, lit = false) => ({
  background: C.panel, border: `1px ${dashed ? 'dashed' : 'solid'} ${color}`, borderRadius: 16, padding: 18,
  display: 'flex', flexDirection: 'column', gap: 12, boxShadow: lit ? `0 0 26px ${glow(C.accent, '22')}` : 'none',
})

const kicker = color => ({ fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color })

function ModeToggle({ mode, onChange, disabled }) {
  const opts = [['breaker', 'Breakers'], ['device', 'Devices']]
  return (
    <div role="radiogroup" aria-label="Hunt mode" style={{ display: 'inline-flex', padding: 3, borderRadius: 12, background: C.field, border: `1px solid ${C.line2}` }}>
      {opts.map(([k, label]) => {
        const on = mode === k
        return (
          <button key={k} type="button" role="radio" aria-checked={on} disabled={disabled} onClick={() => onChange(k)}
            style={{ minHeight: 40, padding: '0 16px', borderRadius: 9, cursor: disabled ? 'default' : 'pointer', fontFamily: MONO, fontSize: 12,
              letterSpacing: 1.2, textTransform: 'uppercase', fontWeight: 700, color: on ? C.accent : C.muted,
              background: on ? alpha(C.accent, '1c') : 'transparent', border: `1px solid ${on ? alpha(C.accent, '55') : 'transparent'}` }}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

const COPY = {
  breaker: { title: 'Breaker hunt', sub: 'Flip breakers one at a time and see exactly what each one feeds.',
    idle: 'Stand at the panel. Tap start, then switch off one breaker.' },
  device: { title: 'Device hunt', sub: 'Switch a device on and off; Senseless measures it and asks what it is.',
    idle: 'Go to a device. Tap start, wait a few seconds, then turn the device on and off.' },
}

export function HuntView({ live, loads }) {
  const { sp } = useTheme()
  const { hunt, send, busy } = useHunt(live.hunt)
  const [pick, setPick] = useState('breaker')     // mode chosen while idle
  const [includeBg, setIncludeBg] = useState(false)
  const mode = hunt?.mode ?? pick
  const circuits = useCircuits(hunt?.last?.breaker ? hunt.version : 0)
  const rooms = useRooms()
  const base = useBaseline()
  const [l1, l2] = live.latest?.channels ?? []
  const alwaysOn = base?.w ?? live.latest?.devices?.find(d => d.name === 'Always On')?.w
  const exp = explained(circuits.list, alwaysOn)
  const healthy = live.status.state === 'live' && live.status.healthy
  const phase = hunt?.phase ?? 'idle'

  const start = m => send('start', { mode: m ?? mode, include_background: includeBg }).catch(() => {})
  const switchMode = m => {
    setPick(m)
    if (phase !== 'idle') send('stop').catch(() => {})
  }

  const drop = hunt?.drop
  const on = hunt?.on
  const legChange = leg => (drop ? drop[`l${leg}`] : on ? on[`l${leg}`] : null)
  const legActive = leg => (drop ? drop.meaningful && Math.abs(drop[`l${leg}`]) >= 15 : on ? on.legs === 'both' || on.legs === `L${leg}` : false)

  return (
    <div style={{ maxWidth: 560, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: sp(14) }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700 }}>{COPY[mode].title}</h1>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{COPY[mode].sub}</div>
        </div>
        <ModeToggle mode={mode} onChange={switchMode} disabled={busy} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
        <Leg leg={1} w={l1} change={legChange(1)} active={legActive(1)} />
        <Leg leg={2} w={l2} change={legChange(2)} active={legActive(2)} />
      </div>

      {!healthy && phase !== 'idle' && (
        <div role="alert" style={{ fontFamily: MONO, fontSize: 12, color: C.amber, padding: '10px 12px', borderRadius: 10, border: `1px solid ${alpha(C.amber, '50')}` }}>
          Live feed stale: readings may be old. Wait for "Live", then retry.
        </div>
      )}

      {phase === 'idle' && (
        <section style={{ ...card(C.line2, true), alignItems: 'center', textAlign: 'center', padding: 20 }}>
          <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>{hunt?.note ? `${hunt.note}.` : COPY[mode].idle}</p>
          <button type="button" onClick={() => start()} disabled={!healthy || busy} style={{ ...bigButton(true), opacity: healthy ? 1 : 0.45 }}>Start</button>
          {mode === 'device' && (
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontFamily: MONO, fontSize: 11, color: C.muted, cursor: 'pointer', textAlign: 'left' }}>
              <input type="checkbox" checked={includeBg} onChange={e => setIncludeBg(e.target.checked)} style={{ width: 18, height: 18, accentColor: C.accent }} />
              Include small loads that cycle all day (to hunt one of those down)
            </label>
          )}
          {!healthy && <div style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>Waiting for the live feed…</div>}
        </section>
      )}
      {phase === 'settling' && (
        <section aria-live="polite" style={{ ...card(C.line2, true), textAlign: 'center', color: C.body, padding: 20 }}>{hunt.prompt}</section>
      )}

      {mode === 'breaker' && phase === 'watching' && drop && <BreakerWatch hunt={hunt} drop={drop} alwaysOn={alwaysOn} send={send} busy={busy} />}
      {mode === 'breaker' && phase === 'flipback' && (
        <section style={{ ...card(alpha(C.green, '50')), alignItems: 'center', textAlign: 'center', padding: 20 }}>
          <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>Switch breaker <b style={{ color: C.bright }}>{hunt.last?.breaker}</b> back on, then tap next.</p>
          <button type="button" onClick={() => start('breaker')} disabled={busy} style={bigButton(true)}>Next breaker</button>
          <button type="button" onClick={() => send('stop').catch(() => {})} style={{ ...bigButton(false), minHeight: 44 }}>Done for now</button>
        </section>
      )}

      {mode === 'device' && ['ready', 'on', 'measured'].includes(phase) && (
        <DeviceStep hunt={hunt} rooms={rooms} send={send} busy={busy} onLabeled={() => loads?.reload?.()} />
      )}

      {mode === 'breaker' ? (
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
      ) : (
        <NamedList patterns={loads?.patterns ?? []} />
      )}

      <p style={{ fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.6 }}>
        Hands busy? A voice assistant can run this same hunt through <code>/api/hunt</code>; this screen follows along.
      </p>
    </div>
  )
}

function BreakerWatch({ hunt, drop, alwaysOn, send, busy }) {
  const [breaker, setBreaker] = useState('')
  const [name, setName] = useState('')
  const save = async e => {
    e.preventDefault()
    if (!breaker.trim()) { toast('Which breaker? e.g. 14'); return }
    try {
      await send('save', { breaker: breaker.trim(), name: name.trim() || null })
      toast(`Breaker ${breaker.trim()} saved`)
      setBreaker(''); setName('')
    } catch { /* toasted */ }
  }
  const retry = () => send('retry').catch(() => {})
  return (
    <section aria-live="polite" style={card(alpha(C.accent, drop.meaningful ? '70' : '30'), false, drop.meaningful)}>
      {drop.meaningful && drop.total > 0 ? (
        <>
          <div style={kicker(C.amber)}>Something turned on</div>
          <div style={{ fontSize: 40, fontWeight: 700, color: C.amber }}>{fmtSigned(drop.total)}</div>
          <p style={{ color: C.body, fontSize: 14, lineHeight: 1.5 }}>
            Load went up, so this wasn't a breaker switching off (an appliance started). Re-baseline, then flip the breaker.
          </p>
          <button type="button" onClick={retry} disabled={busy} style={bigButton(true)}>Re-baseline</button>
        </>
      ) : !drop.meaningful ? (
        <>
          <div style={kicker(C.muted)}>Listening…</div>
          <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>
            Baseline L1 {fmtInt(hunt.baseline?.l1)} W · L2 {fmtInt(hunt.baseline?.l2)} W. Switch off the next breaker; the drop shows here within a second.
          </p>
          <button type="button" onClick={retry} disabled={busy} style={{ ...bigButton(false), minHeight: 44, alignSelf: 'flex-start' }}>Re-baseline</button>
        </>
      ) : (
        <>
          <div style={kicker(C.accent)}>
            {drop.both ? '240 V breaker' : `Dropped on L${Math.abs(drop.l1) > Math.abs(drop.l2) ? 1 : 2}`}
            {drop.steps ? ` · ${drop.steps} step${drop.steps > 1 ? 's' : ''} caught` : ''}
          </div>
          <div style={{ fontSize: 48, fontWeight: 700, color: C.accent, letterSpacing: -1 }}>{fmtSigned(drop.total)}</div>
          {alwaysOn && drop.total < 0 && (
            <p style={{ color: C.body, fontSize: 14 }}>That's <b style={{ color: C.bright }}>{Math.min(100, Math.round((-drop.total / alwaysOn) * 100))}%</b> of your {fmtInt(alwaysOn)} W always-on load.</p>
          )}
          <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 8 }}>
              <label style={fieldLabel}>Breaker
                <input value={breaker} onChange={e => setBreaker(e.target.value)} inputMode="numeric" placeholder="14" maxLength={20} style={inputStyle()} />
              </label>
              <label style={fieldLabel}>What's on it
                <input value={name} onChange={e => setName(e.target.value)} placeholder="Basement" maxLength={60} style={inputStyle()} />
              </label>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button type="submit" disabled={busy} style={bigButton(true)}>Save</button>
              <button type="button" onClick={retry} disabled={busy} style={bigButton(false)}>Re-baseline</button>
            </div>
          </form>
        </>
      )}
    </section>
  )
}

function DeviceStep({ hunt, rooms, send, busy, onLabeled }) {
  const [name, setName] = useState('')
  const [room, setRoom] = useState('')
  const input = useRef(null)
  const m = hunt.measurement
  const phase = hunt.phase
  // back to watching (saved, or measuring again): the next device starts with an empty name
  useEffect(() => { if (phase === 'ready') setName('') }, [phase])

  const label = async e => {
    e?.preventDefault()
    const n = name.trim()
    if (!n) { toast('What is it? Type a name or tap an idea'); input.current?.focus(); return }
    try {
      const r = await send('label', { name: n, ...(room ? { room_id: Number(room) } : {}) })
      toast(r.marker ? `${r.pattern.name} saved and pinned on the map` : `${r.pattern.name} saved`)
      setName('')
      onLabeled?.()
    } catch { /* toasted */ }
  }

  if (phase === 'ready') {
    return (
      <section aria-live="polite" style={{ ...card(alpha(C.accent, hunt.capturing ? '70' : '35'), !hunt.capturing, hunt.capturing), alignItems: 'center', textAlign: 'center', padding: 22 }}>
        <span aria-hidden="true" style={{ width: 14, height: 14, borderRadius: 7, background: C.accent, boxShadow: `0 0 0 6px ${alpha(C.accent, '22')}`,
          animation: 'blink 1.6s ease-in-out infinite' }} />
        <div style={kicker(hunt.capturing ? C.accent : C.muted)}>{hunt.capturing ? 'Something switched… measuring' : 'Watching'}</div>
        <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>{hunt.prompt}</p>
        {hunt.ignoring?.length > 0 && (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.6 }}>
            Ignoring background: {hunt.ignoring.slice(0, 5).map(b => `${b.name ?? `${fmtInt(b.w)} W`} ${b.legs}`).join(' · ')}
            {hunt.ignored_steps ? ` (${hunt.ignored_steps} skipped)` : ''}
          </div>
        )}
        <button type="button" onClick={() => send('stop').catch(() => {})} style={{ ...bigButton(false), minHeight: 44 }}>Done for now</button>
      </section>
    )
  }

  const ideas = []
  for (const x of [...(m?.matches ?? []).filter(x => x.name).map(x => x.name), ...(m?.sense_guess ?? []), ...(m?.ideas ?? [])]) {
    if (!ideas.some(i => i.toLowerCase() === x.toLowerCase())) ideas.push(x)
  }
  const legs = m?.legs === 'both' ? '240 V · both legs' : m?.legs
  return (
    <section aria-live="polite" style={card(alpha(C.accent, '70'), false, true)}>
      <div style={kicker(phase === 'on' ? C.amber : C.accent)}>{phase === 'on' ? 'On now · turn it off' : `Measured · ${m.cycles} cycle${m.cycles === 1 ? '' : 's'}`}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 48, fontWeight: 700, color: C.accent, letterSpacing: -1, fontVariantNumeric: 'tabular-nums' }}>{fmtInt(m.w)}<span style={{ fontSize: 18 }}> W</span></span>
        <span style={{ fontFamily: MONO, fontSize: 12, color: C.muted }}>{legs}{m.stages > 1 ? ' · multi-stage start' : ''}</span>
      </div>
      <p style={{ color: C.body, fontSize: 15, lineHeight: 1.5 }}>{hunt.prompt}</p>
      {m.cycles > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {m.on_sizes.slice(0, m.cycles).map((w, i) => (
            <span key={i} style={{ fontFamily: MONO, fontSize: 11, color: C.body, padding: '4px 8px', borderRadius: 8, border: `1px solid ${C.line2}` }}>
              +{fmtInt(w)} / −{fmtInt(m.off_sizes[i])} W · {fmtSecs(m.durations_s[i])}
            </span>
          ))}
          {m.cycles > 1 && (
            <span style={{ fontFamily: MONO, fontSize: 11, padding: '4px 8px', borderRadius: 8, color: m.consistent ? C.green : C.amber,
              border: `1px solid ${alpha(m.consistent ? C.green : C.amber, '50')}` }}>{m.consistent ? 'consistent' : 'varies'}</span>
          )}
        </div>
      )}
      <form onSubmit={label} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }} aria-label="Ideas">
          {ideas.slice(0, 6).map(x => (
            <button key={x} type="button" onClick={() => setName(x)} aria-pressed={name === x}
              style={{ minHeight: 36, padding: '0 12px', borderRadius: 18, cursor: 'pointer', fontSize: 13,
                color: name === x ? C.bg : C.text, background: name === x ? C.accent : C.field, border: `1px solid ${name === x ? C.accent : C.line2}` }}>{x}</button>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: rooms.length ? 'minmax(0, 1fr) minmax(0, 0.8fr)' : '1fr', gap: 8 }}>
          <label style={fieldLabel}>What is it
            <input ref={input} value={name} onChange={e => setName(e.target.value)} placeholder="Microwave" maxLength={60} style={inputStyle()} />
          </label>
          {rooms.length > 0 && (
            <label style={fieldLabel}>Pin in room
              <select value={room} onChange={e => setRoom(e.target.value)} style={{ ...inputStyle(), padding: '0 8px' }}>
                <option value="">Not on the map</option>
                {rooms.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </label>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="submit" disabled={busy} style={bigButton(true)}>Save name</button>
          <button type="button" onClick={() => send('retry').catch(() => {})} disabled={busy} style={bigButton(false)}>Measure again</button>
        </div>
      </form>
    </section>
  )
}

function NamedList({ patterns }) {
  const named = patterns.filter(p => p.name).sort((a, b) => (b.named_at ?? 0) - (a.named_at ?? 0)).slice(0, 8)
  if (!named.length) return null
  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>Recently named</div>
      {named.map(p => (
        <div key={p.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: '10px 12px' }}>
          <b style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</b>
          <span style={{ fontFamily: MONO, fontSize: 12, color: p.legs === 'L1' ? C.l1 : p.legs === 'L2' ? C.l2 : C.accent }}>
            {p.legs === 'both' ? '240 V' : p.legs} · {fmtInt(p.center_w)} W
          </span>
        </div>
      ))}
    </section>
  )
}

const fieldLabel = { display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.dim, minWidth: 0 }

function inputStyle() {
  return { height: 48, background: C.field, border: `1px solid ${C.line2}`, borderRadius: 10, color: C.text, fontFamily: MONO,
    fontSize: 16, padding: '0 12px', outline: 'none', textTransform: 'none', letterSpacing: 0, minWidth: 0, width: '100%' }
}
