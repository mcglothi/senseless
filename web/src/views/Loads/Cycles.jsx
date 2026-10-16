import { useCallback, useEffect, useState } from 'react'
import { C, MONO, alpha } from '../../ui/theme'
import { toast } from '../../ui/primitives'
import { api } from '../../api/api'
import { fmtInt } from '../../ui/format'
import { cycleIdeas } from '../../ui/loads'

// Appliances that run a program (washers, dishwashers): many small steps that never pair into clean runs.
// The detector groups each program into one cycle; similar cycles share a pattern you can name.

const dur = s => (s == null ? '—' : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.round(s / 60)} min`)
const clock = ts => new Date(ts * 1000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

export function useCycles() {
  const [state, setState] = useState({ patterns: [], cycles: [] })
  const load = useCallback(async () => {
    const [p, cycles] = await Promise.all([api('GET', '/api/cycle-patterns'), api('GET', '/api/cycles?minutes=2880')]).catch(() => [{ patterns: [] }, []])
    setState({ patterns: p.patterns ?? [], cycles })
  }, [])
  useEffect(() => { load(); const id = setInterval(load, 60000); return () => clearInterval(id) }, [load])
  const name = async (id, n) => { await api('POST', `/api/cycle-patterns/${id}/name`, { name: n }); await load() }
  const ignore = async id => { await api('POST', `/api/cycle-patterns/${id}/ignore`, { ignored: true }); await load() }
  return { ...state, name, ignore }
}

export function Cycles() {
  const { patterns, cycles, name, ignore } = useCycles()
  const shown = patterns.filter(p => !p.ignored)
  if (!shown.length) return null
  return (
    <section aria-label="Cycles" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 2.5, textTransform: 'uppercase', color: C.accent }}>Cycles · appliances that run a program</div>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>washers, dishwashers: one event per run, not dozens of fragments</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
        {shown.map(p => <CycleCard key={p.id} p={p} recent={cycles.filter(c => c.pattern === p.id).sort((a, b) => b.start_ts - a.start_ts)} onName={name} onIgnore={ignore} />)}
      </div>
    </section>
  )
}

function CycleCard({ p, recent, onName, onIgnore }) {
  const [draft, setDraft] = useState(p.name ?? '')
  useEffect(() => setDraft(p.name ?? ''), [p.name])
  const last = recent[0]
  const ideas = cycleIdeas(p, last)
  const save = async n => {
    const v = (n ?? draft).trim()
    try { await onName(p.id, v || null); toast(v ? `Named ${v}` : 'Name cleared') } catch (e) { toast(e.message) }
  }
  const legColor = p.legs === 'L1' ? C.l1 : p.legs === 'L2' ? C.l2 : C.accent
  return (
    <div style={{ border: `1px solid ${p.name ? alpha(C.accent, '50') : C.line2}`, borderRadius: 12, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 16, fontWeight: 700, color: p.name ? C.bright : C.body }}>{p.name ?? 'Unnamed cycle'}</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: legColor }}>{p.legs === 'both' ? '240 V' : p.legs}</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.6 }}>
        about {dur(p.median_duration_s ?? p.duration_s)} · ~{fmtInt(p.energy_wh)} Wh a run · steps of ~{fmtInt(p.median_step_w)} W
        <br />{p.cycles_today} today · {p.cycles_window} in 48 h{last ? ` · last ${clock(last.start_ts)}–${clock(last.end_ts)}` : ''}
        {last?.has_heat ? ' · heats water' : ''}
      </div>
      {!p.name && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {ideas.map(x => (
            <button key={x} type="button" onClick={() => save(x)} style={{ minHeight: 30, padding: '0 10px', borderRadius: 15, cursor: 'pointer', fontSize: 12, color: C.text, background: C.field, border: `1px solid ${C.line2}` }}>{x}</button>
          ))}
        </div>
      )}
      <form onSubmit={e => { e.preventDefault(); save() }} style={{ display: 'flex', gap: 6 }}>
        <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="What is it?" maxLength={60} aria-label="Cycle name"
          style={{ flex: 1, minWidth: 0, height: 34, background: C.field, border: `1px solid ${C.line2}`, borderRadius: 8, color: C.text, fontSize: 13, padding: '0 10px' }} />
        <button type="submit" style={{ height: 34, padding: '0 12px', borderRadius: 8, cursor: 'pointer', fontFamily: MONO, fontSize: 11, color: C.accent, background: alpha(C.accent, '14'), border: `1px solid ${alpha(C.accent, '50')}` }}>Save</button>
        {!p.name && <button type="button" onClick={() => onIgnore(p.id)} style={{ height: 34, padding: '0 10px', borderRadius: 8, cursor: 'pointer', fontFamily: MONO, fontSize: 11, color: C.dim, background: 'transparent', border: `1px solid ${C.line2}` }}>Ignore</button>}
      </form>
    </div>
  )
}
