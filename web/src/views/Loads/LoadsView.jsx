import { useEffect, useMemo, useRef, useState } from 'react'
import { C, MONO, alpha, glow, useTheme } from '../../ui/theme'
import { SectionTitle, Empty, toast } from '../../ui/primitives'
import { busyTyping } from '../../api/api'
import { fmtClock, fmtInt } from '../../ui/format'
import { existingNames, fingerprintPath, fmtDuration, groupNamed, inboxOf, legsLabel, nameIdeas } from '../../ui/loads'

// Loads: the patterns detection found, an inbox of the ones worth naming, and the ones
// you've named. Naming is the whole point: once named, every future match carries it.

const legColor = legs => (legs === 'both' ? C.violet : legs === 'L1' ? C.l1 : C.l2)

export function Fingerprint({ p, height = 48, width = 96, stroke = 2 }) {
  const c = legColor(p.legs)
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img"
      aria-label={`${legsLabel(p.legs)} ${fmtInt(p.center_w)} W step pattern`} style={{ display: 'block' }}>
      <rect width={width} height={height} rx="8" fill={C.field} />
      <path d={fingerprintPath(p, width, height)} fill="none" stroke={c} strokeWidth={stroke} strokeLinejoin="round"
        vectorEffect="non-scaling-stroke" style={{ filter: `drop-shadow(0 0 4px ${glow(c, '60')})` }} />
    </svg>
  )
}

function Meta({ p }) {
  const c = legColor(p.legs)
  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontFamily: MONO, fontSize: 11, color: C.muted }}>
      <span style={{ color: c }}>{legsLabel(p.legs)} · {fmtInt(p.center_w)} W</span>
      <span>{p.runs_window}× in 48 h</span>
      <span>{p.median_duration_s != null ? `typically ${fmtDuration(p.median_duration_s)}` : 'off not seen yet'}</span>
      {p.wh_today > 0 && <span>{p.wh_today >= 1000 ? `${(p.wh_today / 1000).toFixed(2)} kWh` : `${fmtInt(p.wh_today)} Wh`} today</span>}
      {p.running && <span style={{ color: C.green }}>● on now</span>}
    </div>
  )
}

function InboxCard({ p, focused, onFocus, onName, onIgnore, inputRef, known = [] }) {
  const [text, setText] = useState('')
  const submit = async name => {
    const n = (name ?? text).trim()
    if (!n) return
    try { await onName(p, n); toast(`Named "${n}"`) } catch (e) { toast(`Couldn't save: ${e.message}`) }
  }
  return (
    <div onClick={onFocus} style={{
      display: 'grid', gridTemplateColumns: 'minmax(96px, 120px) 1fr', gap: 14, alignItems: 'center', padding: 12, borderRadius: 12,
      background: C.field, border: `1px solid ${focused ? alpha(C.accent, '70') : C.line}`,
      boxShadow: focused ? `0 0 16px ${glow(C.accent, '25')}` : 'none', transition: 'border-color .15s, box-shadow .15s',
    }}>
      <Fingerprint p={p} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: 15 }}>{p.legs === 'both' ? '240 V appliance' : `${fmtInt(p.center_w)} W on ${p.legs}`}</span>
          {p.last_ts > 0 && <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>last {fmtClock(p.last_ts, { seconds: false })}</span>}
        </div>
        <Meta p={p} />
        <form onSubmit={e => { e.preventDefault(); submit() }} style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <label htmlFor={`name-${p.id}`} style={{ position: 'absolute', left: -9999 }}>Name this load</label>
          <input id={`name-${p.id}`} ref={inputRef} value={text} onChange={e => setText(e.target.value)} placeholder="What is it?"
            onFocus={onFocus}
            style={{
              flex: '1 1 140px', minWidth: 0, height: 34, background: C.panel, border: `1px solid ${C.line2}`, borderRadius: 8,
              color: C.text, fontFamily: MONO, fontSize: 12, padding: '0 10px', outline: 'none',
            }} />
          {p.sense_hint && !known.some(k => k.toLowerCase() === p.sense_hint.name.toLowerCase()) && (
            <button type="button" onClick={() => submit(p.sense_hint.name)}
              title={`Sense's own detection turned "${p.sense_hint.name}" on at the same moment in ${p.sense_hint.matches} of these runs`}
              style={{ cursor: 'pointer', minHeight: 32, fontFamily: MONO, fontSize: 11, padding: '4px 10px', borderRadius: 16,
                color: C.green, background: alpha(C.green, '10'), border: `1px solid ${alpha(C.green, '50')}` }}>
              Sense: {p.sense_hint.name} · {Math.round(p.sense_hint.share * 100)}%
            </button>
          )}
          {known.map(n => (
            <button key={`k-${n}`} type="button" onClick={() => submit(n)} title="Already a named load on these legs" style={{
              cursor: 'pointer', minHeight: 32, fontFamily: MONO, fontSize: 11, padding: '4px 10px', borderRadius: 16,
              color: C.accent, background: alpha(C.accent, '10'), border: `1px solid ${alpha(C.accent, '45')}`,
            }}>= {n}</button>
          ))}
          {nameIdeas(p).filter(i => !known.some(k => k.toLowerCase() === i.toLowerCase())).map(idea => (
            <button key={idea} type="button" onClick={() => submit(idea)} style={{
              cursor: 'pointer', minHeight: 32, fontFamily: MONO, fontSize: 11, padding: '4px 10px', borderRadius: 16,
              color: C.body, background: 'transparent', border: `1px solid ${C.line2}`,
            }}>{idea}</button>
          ))}
          <button type="button" onClick={() => onIgnore(p).then(() => toast('Ignored'))} style={{
            cursor: 'pointer', minHeight: 32, fontFamily: MONO, fontSize: 11, padding: '4px 10px', borderRadius: 16,
            color: C.dim, background: 'transparent', border: '1px solid transparent',
          }}>ignore</button>
        </form>
      </div>
    </div>
  )
}

function NamedCard({ p, onRename }) {
  const { cardRadius } = useTheme()
  return (
    <div style={{ background: C.panel, border: `1px solid ${p.running ? alpha(C.green, '50') : C.line}`, borderRadius: cardRadius + 4,
      padding: 16, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
        <span style={{ fontWeight: 600, fontSize: 16, color: C.bright }}>{p.name}</span>
        <button type="button" onClick={() => onRename(p)} aria-label={`Rename ${p.name}`} style={{
          cursor: 'pointer', fontFamily: MONO, fontSize: 10, padding: '3px 8px', borderRadius: 12, color: C.muted,
          background: 'transparent', border: `1px solid ${C.line2}`,
        }}>rename</button>
      </div>
      <Fingerprint p={p} height={64} stroke={1.6} />
      <Meta p={p} />
      {p.members?.length > 1 && (
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
          {p.members.length} patterns: {p.members.map(m => `${legsLabel(m.legs)} ${fmtInt(m.center_w)} W`).join(' · ')}
        </div>
      )}
    </div>
  )
}

export function LoadsView({ loads }) {
  const { sp } = useTheme()
  const { patterns, name, ignore, error, computed_at } = loads
  const inbox = useMemo(() => inboxOf(patterns), [patterns])
  const named = useMemo(() => groupNamed(patterns), [patterns])
  const running = useMemo(() => patterns.filter(p => p.running && !p.ignored), [patterns])
  const ignoredCount = patterns.filter(p => p.ignored && !p.name).length
  const [focus, setFocus] = useState(0)
  const inputs = useRef({})

  // J/K move through the inbox, Enter names the focused one, X ignores it
  useEffect(() => {
    const onKey = e => {
      if (e.metaKey || e.ctrlKey || e.altKey || !inbox.length) return
      // keys on a focused button/link/field belong to it (Enter must still activate a button)
      if (e.target instanceof Element && e.target.closest('button, a, input, textarea, select, [role=button]') && !busyTyping()) return
      if (busyTyping()) { if (e.key === 'Escape') document.activeElement.blur(); return }
      const k = e.key.toLowerCase()
      if (k === 'j') setFocus(f => Math.min(inbox.length - 1, f + 1))
      else if (k === 'k') setFocus(f => Math.max(0, f - 1))
      else if (e.key === 'Enter') { e.preventDefault(); inputs.current[inbox[Math.min(focus, inbox.length - 1)]?.id]?.focus() }
      else if (k === 'x') { const p = inbox[Math.min(focus, inbox.length - 1)]; if (p) ignore(p).then(() => toast('Ignored')) }
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [inbox, focus, ignore])

  const rename = p => {
    const n = window.prompt(`Rename "${p.name}" (empty to un-name)`, p.name)
    if (n === null) return
    // a grouped card renames every pattern in the group
    Promise.all((p.members ?? [p]).map(m => name(m, n.trim() || null)))
      .then(() => toast(n.trim() ? `Renamed "${n.trim()}"` : 'Back in the inbox'))
  }

  if (error && !patterns.length) return <Empty title="Couldn't load patterns">{error}</Empty>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: sp(18) }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 26, fontWeight: 700 }}>Loads</h1>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
          grouped from on-board steps by legs, size and shape{computed_at ? ` · updated ${fmtClock(computed_at, { seconds: false })}` : ''}
        </span>
      </div>

      {running.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: C.green, textTransform: 'uppercase' }}>On now</span>
          {running.map(p => (
            <span key={p.id} style={{ fontFamily: MONO, fontSize: 11, padding: '5px 10px', borderRadius: 20,
              color: legColor(p.legs), border: `1px solid ${alpha(legColor(p.legs), '50')}`, background: alpha(legColor(p.legs), '10') }}>
              {p.name ?? `${legsLabel(p.legs)} ${fmtInt(p.center_w)} W`}
            </span>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: sp(18), alignItems: 'flex-start' }}>
        <section aria-label="Inbox" style={{ flex: '3 1 520px', minWidth: 0, background: C.panel, border: `1px solid ${alpha(C.accent, '40')}`,
          borderRadius: 16, padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <SectionTitle style={{ margin: 0 }} right={<span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>J/K move · Enter name · X ignore</span>}>
            Inbox · {inbox.length} to name
          </SectionTitle>
          {inbox.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', fontFamily: MONO, fontSize: 12, color: C.muted }}>
              Inbox zero. New patterns show up here once they repeat.
            </div>
          )}
          {inbox.slice(0, 12).map((p, i) => (
            <InboxCard key={p.id} p={p} focused={i === focus} onFocus={() => setFocus(i)} onName={name} onIgnore={ignore} known={existingNames(patterns, p)}
              inputRef={el => { inputs.current[p.id] = el }} />
          ))}
          {inbox.length > 12 && <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>+{inbox.length - 12} more, most energy first</div>}
          {ignoredCount > 0 && <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>{ignoredCount} ignored</div>}
        </section>

        <section aria-label="Named loads" style={{ flex: '2 1 340px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <SectionTitle style={{ margin: 0 }}>Named · {named.length}</SectionTitle>
          {named.length === 0 ? (
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.muted, padding: 12 }}>
              Nothing named yet. Name a pattern in the inbox and every future match gets that name.
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 260px), 1fr))', gap: 14 }}>
              {named.map(p => <NamedCard key={p.id} p={p} onRename={rename} />)}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
