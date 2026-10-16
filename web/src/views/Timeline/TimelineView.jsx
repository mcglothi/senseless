import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { C, MONO, alpha, glow, useTheme } from '../../ui/theme'
import { toast } from '../../ui/primitives'
import { api, busyTyping } from '../../api/api'
import { fmtClock, fmtInt, fmtSigned } from '../../ui/format'
import { fmtDuration, legsLabel, nameIdeas } from '../../ui/loads'
import { minRunW, packRows, ticksFor, zoomBucket } from '../../ui/timeline'
import { Fingerprint } from '../Loads/LoadsView'

// Timeline: power per leg, every step, and every detected run, across any window.
// Zoom with the chips or by dragging across the chart; ←/→ pan; L follows live.

const ZOOMS = [['24h', 86400], ['6h', 21600], ['3h', 10800], ['1h', 3600], ['15m', 900], ['2m', 120]]
const LANES = { l1: 70, l2: 90, steps: 34, runRow: 22 }
const GUTTER = 64   // left labels
const legColor = legs => (legs === 'both' ? C.violet : legs === 'L1' ? C.l1 : C.l2)

function useWindowData(win) {
  const [data, setData] = useState({ series: [], deltas: [], runs: [], loading: true })
  useEffect(() => {
    let alive = true
    const start = win.end - win.span, end = win.end
    const bucket = zoomBucket(win.span)
    setData(d => ({ ...d, loading: true }))
    Promise.all([
      api('GET', `/api/series?start=${start}&end=${end}&bucket=${bucket}`),
      api('GET', `/api/deltas?start=${start}&end=${end}`),
      api('GET', `/api/runs?start=${start}&end=${end}`).catch(() => []),
    ]).then(([series, deltas, runs]) => { if (alive) setData({ series, deltas, runs, loading: false }) })
      // never leave the previous window's data plotted against the new axes
      .catch(e => { if (alive) { setData({ series: [], deltas: [], runs: [], loading: false }); toast(`Couldn't load: ${e.message}`) } })
    return () => { alive = false }
  }, [win.end, win.span])
  return data
}

function Chart({ win, data, patterns, onZoomTo, selected, onSelect, allRuns }) {
  const ref = useRef(null)
  const [width, setWidth] = useState(1000)
  const [hover, setHover] = useState(null)
  const [drag, setDrag] = useState(null)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(360, Math.round(e.contentRect.width))))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  const W = width - GUTTER - 8
  const start = win.end - win.span
  const X = t => GUTTER + ((t - start) / win.span) * W
  const T = x => start + ((x - GUTTER) / W) * win.span
  const pts = data.series.filter(b => b.l1 != null)
  const max1 = Math.max(100, ...pts.map(b => b.l1)) * 1.1
  const max2 = Math.max(100, ...pts.map(b => b.l2)) * 1.1
  const area = (key, max, h) => pts.length < 2 ? '' :
    `M${X(pts[0].t).toFixed(1)},${h} ` + pts.map(b => `L${X(b.t).toFixed(1)},${(h - (b[key] / max) * (h - 4)).toFixed(1)}`).join(' ') +
    ` L${X(pts[pts.length - 1].t).toFixed(1)},${h} Z`
  const y0 = 18, yL2 = y0 + LANES.l1 + 14, ySteps = yL2 + LANES.l2 + 14, yRuns = ySteps + LANES.steps + 14
  const names = useMemo(() => new Map(patterns.map(p => [p.id, p])), [patterns])
  const minW = allRuns ? 0 : minRunW(win.span)
  const shown = useMemo(() => data.runs.filter(r => r.w >= minW || names.get(r.pattern)?.name), [data.runs, minW, names])
  const rows = useMemo(() => packRows(shown, win.end, 5, (8 / W) * win.span), [shown, win.end, W, win.span])
  const nRows = Math.max(1, ...rows.map(r => r.row + 1))
  const H = yRuns + nRows * (LANES.runRow + 6) + 30
  const ticks = ticksFor(win.span, start, win.end)
  const steps = data.deltas.filter(d => Math.abs(d.w) >= 30)
  const dense = steps.length > W / 2
  const bins = useMemo(() => {
    if (!dense) return null
    const n = Math.max(1, Math.floor(W / 4)), b = [new Array(n).fill(0), new Array(n).fill(0)]
    for (const d of steps) b[d.channel][Math.min(n - 1, Math.max(0, Math.floor(((d.ts - start) / win.span) * n)))]++
    return { n, b, max: Math.max(1, ...b[0], ...b[1]) }
  }, [dense, steps, start, win.span, W])

  const nearest = t => {
    let best = null
    for (const b of pts) if (!best || Math.abs(b.t - t) < Math.abs(best.t - t)) best = b
    return best
  }
  const onMove = e => {
    const r = e.currentTarget.getBoundingClientRect(), x = e.clientX - r.left
    if (x < GUTTER) { setHover(null); return }
    setHover({ x, t: T(x) })
    if (drag) setDrag(d => ({ ...d, x2: x }))
  }
  const onUp = () => {
    if (drag && Math.abs(drag.x2 - drag.x1) > 8) {
      const a = T(Math.min(drag.x1, drag.x2)), b = T(Math.max(drag.x1, drag.x2))
      onZoomTo(a, b)
    }
    setDrag(null)
  }
  const hb = hover && nearest(hover.t)

  return (
    <div ref={ref} style={{ width: '100%', position: 'relative' }}>
      <svg width={width} height={H} role="group" aria-label="Timeline of per-leg power, steps and detected runs. Runs are buttons: Tab to them, Enter to inspect."
        onMouseMove={onMove} onMouseLeave={() => { setHover(null); setDrag(null) }}
        onMouseDown={e => { const x = e.clientX - e.currentTarget.getBoundingClientRect().left; if (x >= GUTTER) setDrag({ x1: x, x2: x }) }}
        onMouseUp={onUp} style={{ display: 'block', userSelect: 'none', cursor: drag ? 'col-resize' : 'crosshair' }}>
        {ticks.map(t => (
          <g key={t.t}>
            <line x1={X(t.t)} x2={X(t.t)} y1={y0 - 6} y2={H - 22} stroke={C.line} strokeDasharray="2 6" />
            <text x={X(t.t)} y={H - 6} textAnchor="middle" fontSize="10" fontFamily="JetBrains Mono, monospace" fill={C.dim}>{t.label}</text>
          </g>
        ))}
        {[['L1 POWER', y0, C.l1, max1], ['L2 POWER', yL2, C.l2, max2]].map(([label, y, c, mx]) => (
          <g key={label}>
            <text x="0" y={y + 10} fontSize="10" letterSpacing="1.5" fontFamily="JetBrains Mono, monospace" fill={c}>{label.split(' ')[0]}</text>
            <text x="0" y={y + 24} fontSize="9" fontFamily="JetBrains Mono, monospace" fill={C.dim}>{mx >= 1000 ? `${(mx / 1000).toFixed(1)} kW` : `${fmtInt(mx)} W`}</text>
          </g>
        ))}
        <g transform={`translate(0,${y0})`}><path d={area('l1', max1, LANES.l1)} fill={alpha(C.l1, '38')} stroke={C.l1} strokeWidth="1.2" /></g>
        <g transform={`translate(0,${yL2})`}><path d={area('l2', max2, LANES.l2)} fill={alpha(C.l2, '30')} stroke={C.l2} strokeWidth="1.2" /></g>
        <text x="0" y={ySteps + 12} fontSize="10" letterSpacing="1.5" fontFamily="JetBrains Mono, monospace" fill={C.muted}>STEPS</text>
        <line x1={GUTTER} x2={GUTTER + W} y1={ySteps + LANES.steps / 2} y2={ySteps + LANES.steps / 2} stroke={C.line} />
        {dense ? bins.b.map((row, ch) => row.map((n, i) => n ? (
          <rect key={`${ch}-${i}`} x={GUTTER + (i / bins.n) * W} width={Math.max(1, W / bins.n - 1)}
            y={ch === 0 ? ySteps + LANES.steps / 2 - (n / bins.max) * (LANES.steps / 2 - 2) : ySteps + LANES.steps / 2}
            height={(n / bins.max) * (LANES.steps / 2 - 2)} fill={alpha(ch === 0 ? C.l1 : C.l2, 'aa')} />
        ) : null)) : steps.map((d, i) => {
          const c = d.channel === 0 ? C.l1 : C.l2, mid = ySteps + LANES.steps / 2, h = Math.min(14, 4 + Math.log10(Math.abs(d.w)) * 3)
          return <line key={i} x1={X(d.ts)} x2={X(d.ts)} y1={d.channel === 0 ? mid - h : mid} y2={d.channel === 0 ? mid : mid + h}
            stroke={d.w > 0 ? c : alpha(c, '70')} strokeWidth="2" strokeLinecap="round" />
        })}
        <text x="0" y={yRuns + 14} fontSize="10" letterSpacing="1.5" fontFamily="JetBrains Mono, monospace" fill={C.muted}>RUNS</text>
        {rows.map(({ run: r, row }) => {
          const p = names.get(r.pattern), c = legColor(r.legs)
          const x1 = Math.max(GUTTER, X(r.start_ts)), x2 = Math.min(GUTTER + W, X(r.end_ts ?? (r.running ? win.end : r.start_ts)))
          const w = Math.max(4, x2 - x1), y = yRuns + row * (LANES.runRow + 6)
          const label = p?.name ?? `${legsLabel(r.legs)} ${fmtInt(r.w)} W`
          const isSel = selected && selected.start_frame === r.start_frame && selected.legs === r.legs && selected.generation === r.generation
          return (
            <g key={`${r.generation ?? 0}-${r.start_frame}-${r.legs}`} onClick={e => { e.stopPropagation(); onSelect(r) }} style={{ cursor: 'pointer', outline: 'none' }}
              onMouseDown={e => e.stopPropagation()} tabIndex={0} role="button"
              aria-label={`${label}, ${fmtClock(r.start_ts, { seconds: false })}, ${r.status === 'unresolved' ? 'end not seen' : fmtDuration(r.duration_s)}`}
              aria-pressed={isSel} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r) } }}
              onFocus={e => e.currentTarget.querySelector('rect')?.setAttribute('stroke-width', '3')}
              onBlur={e => e.currentTarget.querySelector('rect')?.setAttribute('stroke-width', isSel ? '2' : '1')}>
              <rect x={x1} y={y} width={w} height={LANES.runRow} rx="5" fill={alpha(c, isSel ? '55' : r.status === 'unresolved' ? '10' : '26')}
                stroke={c} strokeWidth={isSel ? 2 : 1} strokeDasharray={r.status === 'unresolved' ? '3 3' : undefined} />
              {w > 46 && <text x={x1 + 6} y={y + 15} fontSize="10.5" fontFamily="JetBrains Mono, monospace" fill={C.text}
                style={{ pointerEvents: 'none' }}>{label.length * 6.4 > w - 10 ? label.slice(0, Math.max(3, Math.floor((w - 14) / 6.4))) + '…' : label}</text>}
              <title>{`${label} · ${fmtClock(r.start_ts, { seconds: false })} · ${r.status === 'unresolved' ? 'end not seen' : fmtDuration(r.duration_s)}`}</title>
            </g>
          )
        })}
        {data.runs.length === 0 && !data.loading && (
          <text x={GUTTER} y={yRuns + 15} fontSize="11" fontFamily="JetBrains Mono, monospace" fill={C.dim}>
            {win.end < Date.now() / 1000 - 48 * 3600 ? 'runs are kept for the last 48 h' : 'no runs in this window'}
          </text>
        )}
        {drag && <rect x={Math.min(drag.x1, drag.x2)} y={y0 - 6} width={Math.abs(drag.x2 - drag.x1)} height={H - y0 - 16}
          fill={alpha(C.accent, '14')} stroke={alpha(C.accent, '70')} strokeDasharray="4 4" />}
        {hover && !drag && (
          <line x1={hover.x} x2={hover.x} y1={y0 - 6} y2={H - 22} stroke={alpha(C.text, '50')} style={{ pointerEvents: 'none' }} />
        )}
      </svg>
      {hover && hb && !drag && (
        <div style={{
          position: 'absolute', top: 4, left: Math.min(hover.x + 12, width - 200), pointerEvents: 'none', zIndex: 2,
          background: alpha(C.field, 'f0'), border: `1px solid ${C.line2}`, borderRadius: 8, padding: '6px 10px',
          fontFamily: MONO, fontSize: 11, color: C.body, boxShadow: '0 8px 24px var(--shade)', whiteSpace: 'nowrap',
        }}>
          <div style={{ color: C.text }}>{fmtClock(hover.t)}</div>
          <div><span style={{ color: C.l1 }}>L1</span> {fmtInt(hb.l1)} W · <span style={{ color: C.l2 }}>L2</span> {fmtInt(hb.l2)} W</div>
          <div style={{ color: C.muted }}>total {fmtInt(hb.w)} W{hb.w_max > hb.w * 1.2 ? ` · peak ${fmtInt(hb.w_max)} W` : ''}</div>
        </div>
      )}
    </div>
  )
}

function Inspector({ run, patterns, onName, onZoom, onClose }) {
  const p = patterns.find(x => x.id === run.pattern)
  const c = legColor(run.legs)
  const [text, setText] = useState('')
  useEffect(() => setText(''), [run])
  const submit = async name => {
    const n = (name ?? text).trim()
    if (!n || !p) return
    try { await onName(p, n); toast(`Named "${n}"`) } catch (e) { toast(`Couldn't save: ${e.message}`) }
  }
  return (
    <aside aria-label="Selected run" style={{ flex: '1 1 300px', minWidth: 0, background: C.panel, border: `1px solid ${alpha(c, '55')}`,
      borderRadius: 16, padding: 18, display: 'flex', flexDirection: 'column', gap: 12, boxShadow: `0 0 24px ${glow(c, '18')}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 2.5, textTransform: 'uppercase', color: c }}>Run · {fmtClock(run.start_ts)}</span>
        <span style={{ flex: 1 }} />
        <button type="button" onClick={onClose} aria-label="Close" style={{ width: 30, height: 30, borderRadius: 8, cursor: 'pointer',
          background: 'transparent', color: C.muted, border: `1px solid ${C.line2}` }}>×</button>
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color: C.bright }}>{p?.name ?? `${legsLabel(run.legs)} · ${fmtInt(run.w)} W`}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8, fontFamily: MONO, fontSize: 12 }}>
        {[
          ['Size', `${fmtInt(run.w)} W`],
          ['Legs', legsLabel(run.legs)],
          ['Duration', run.status === 'unresolved' ? 'end not seen' : fmtDuration(run.duration_s) + (run.running ? ' (on)' : '')],
          ['Energy', run.status === 'unresolved' ? '—' : `${run.wh >= 1000 ? (run.wh / 1000).toFixed(2) + ' kWh' : fmtInt(run.wh) + ' Wh'}`],
        ].map(([k, v]) => (
          <div key={k} style={{ background: C.field, border: `1px solid ${C.line}`, borderRadius: 10, padding: '8px 10px' }}>
            <div style={{ color: C.dim, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase' }}>{k}</div>
            <div style={{ color: C.text, fontSize: 15, fontWeight: 700 }}>{v}</div>
          </div>
        ))}
      </div>
      {run.stages?.length > 1 && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>
          Stages: {run.stages.map(([f, w], i) => <span key={i}>{i ? ' → ' : ''}{fmtSigned(w)}{f ? ` @${(f / 60).toFixed(1)} s` : ''}</span>)}
          {run.off_w != null && <span> · off {fmtSigned(run.off_w)}</span>}
        </div>
      )}
      {p && <Fingerprint p={p} height={56} stroke={1.6} />}
      {p && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>
          Pattern #{p.id}: {p.runs_window}× in 48 h{p.median_duration_s != null ? `, typically ${fmtDuration(p.median_duration_s)}` : ''}
        </div>
      )}
      {p && !p.name && (
        <form onSubmit={e => { e.preventDefault(); submit() }} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label htmlFor="run-name" style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.dim }}>What is it?</label>
          <input id="run-name" value={text} onChange={e => setText(e.target.value)} placeholder="Name this load" style={{
            height: 40, background: C.field, border: `1px solid ${C.line2}`, borderRadius: 10, color: C.text, fontFamily: MONO, fontSize: 13, padding: '0 12px', outline: 'none' }} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {p.sense_hint && (
              <button type="button" onClick={() => submit(p.sense_hint.name)} title="Sense's detection turned this on at the same moments"
                style={{ cursor: 'pointer', minHeight: 34, fontFamily: MONO, fontSize: 11, padding: '5px 11px', borderRadius: 18,
                  color: C.green, background: alpha(C.green, '10'), border: `1px solid ${alpha(C.green, '50')}` }}>Sense: {p.sense_hint.name}</button>
            )}
            {nameIdeas(p).map(idea => (
              <button key={idea} type="button" onClick={() => submit(idea)} style={{ cursor: 'pointer', minHeight: 34, fontFamily: MONO, fontSize: 11,
                padding: '5px 11px', borderRadius: 18, color: C.body, background: 'transparent', border: `1px solid ${C.line2}` }}>{idea}</button>
            ))}
          </div>
        </form>
      )}
      <button type="button" onClick={onZoom} style={{ alignSelf: 'flex-start', cursor: 'pointer', fontFamily: MONO, fontSize: 11, letterSpacing: 1,
        textTransform: 'uppercase', padding: '9px 13px', borderRadius: 8, color: C.accent, background: alpha(C.accent, '14'), border: `1px solid ${alpha(C.accent, '45')}` }}>
        Zoom to run
      </button>
    </aside>
  )
}

export function TimelineView({ loads }) {
  const { sp } = useTheme()
  const [win, setWin] = useState(() => ({ end: Math.floor(Date.now() / 1000), span: 10800, live: true }))
  const [selectedRaw, setSelected] = useState(null)
  const [allRuns, setAllRuns] = useState(false)
  const data = useWindowData(win)
  // the inspector shows the latest copy of the selected run, so "on now" / duration stay current
  const sameRun = (a, b) => a && b && a.start_frame === b.start_frame && a.legs === b.legs && (a.generation ?? 0) === (b.generation ?? 0)
  const selected = useMemo(() => (selectedRaw && data.runs.find(r => sameRun(r, selectedRaw))) || selectedRaw, [selectedRaw, data.runs])

  // live: slide the window to now every 15 s
  useEffect(() => {
    if (!win.live) return
    const id = setInterval(() => setWin(w => ({ ...w, end: Math.floor(Date.now() / 1000) })), 15000)
    return () => clearInterval(id)
  }, [win.live])

  const zoomTo = useCallback((a, b) => setWin({ end: Math.ceil(b), span: Math.max(60, Math.round(b - a)), live: false }), [])
  const pan = useCallback(dir => setWin(w => {
    const end = Math.min(Math.floor(Date.now() / 1000), w.end + dir * w.span / 2)
    return { ...w, end, live: end >= Date.now() / 1000 - 5 }
  }), [])
  const setSpan = span => setWin(w => ({ end: w.live ? Math.floor(Date.now() / 1000) : w.end, span, live: w.live }))
  const goLive = () => setWin(w => ({ ...w, end: Math.floor(Date.now() / 1000), live: true }))

  useEffect(() => {
    const onKey = e => {
      if (busyTyping() || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'ArrowLeft') pan(-1)
      else if (e.key === 'ArrowRight') pan(1)
      else if (e.key.toLowerCase() === 'l' && !e.shiftKey) goLive()
      else if (e.key === 'Escape') setSelected(null)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pan])

  const chip = on => ({
    cursor: 'pointer', fontFamily: MONO, fontSize: 11, letterSpacing: 1, padding: '7px 11px', borderRadius: 7,
    color: on ? C.accent : C.muted, background: on ? alpha(C.accent, '14') : 'transparent', border: `1px solid ${on ? alpha(C.accent, '50') : 'transparent'}`,
  })
  const start = win.end - win.span
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: sp(16) }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
        <h1 style={{ fontSize: 26, fontWeight: 700 }}>Timeline</h1>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
          {fmtClock(start, { seconds: false })} → {win.live ? 'now' : fmtClock(win.end, { seconds: false })}
          {start < Date.now() / 1000 - 86400 ? ` · ${new Date(start * 1000).toLocaleDateString()}` : ''} · {data.deltas.length} steps · {data.runs.length} runs
          {data.loading ? ' · loading…' : ''}
        </span>
        <span style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 4, padding: 4, border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel }}>
          <button type="button" onClick={() => pan(-1)} aria-label="Earlier (←)" title="Earlier (←)" style={chip(false)}>←</button>
          <button type="button" onClick={() => pan(1)} aria-label="Later (→)" title="Later (→)" disabled={win.live} style={{ ...chip(false), opacity: win.live ? 0.4 : 1 }}>→</button>
          <button type="button" onClick={goLive} style={chip(win.live)} title="Follow live (L)">Live</button>
        </div>
        <div role="group" aria-label="Zoom" style={{ display: 'flex', gap: 4, padding: 4, border: `1px solid ${C.line}`, borderRadius: 10, background: C.panel }}>
          {ZOOMS.map(([label, s]) => <button key={label} type="button" onClick={() => setSpan(s)} style={chip(win.span === s)} aria-pressed={win.span === s}>{label}</button>)}
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: sp(16), alignItems: 'flex-start' }}>
        <section style={{ flex: '999 1 640px', minWidth: 0, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 16, padding: '16px 18px 10px' }}>
          <Chart win={win} data={data} patterns={loads.patterns} onZoomTo={zoomTo} selected={selected} onSelect={setSelected} allRuns={allRuns} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: MONO, fontSize: 10, color: C.dim, padding: '2px 0 4px' }}>
            <span>{allRuns ? 'all runs' : `runs ≥ ${minRunW(win.span)} W, plus named`}</span>
            <button type="button" onClick={() => setAllRuns(a => !a)} aria-pressed={allRuns} style={{ cursor: 'pointer', fontFamily: MONO, fontSize: 10,
              padding: '3px 8px', borderRadius: 10, color: C.muted, background: 'transparent', border: `1px solid ${C.line2}` }}>
              {allRuns ? 'filter small' : 'show all'}
            </button>
          </div>
        </section>
        {selected && (
          <Inspector run={selected} patterns={loads.patterns} onName={loads.name} onClose={() => setSelected(null)}
            onZoom={() => {
              const end = selected.end_ts ?? (selected.running ? Date.now() / 1000 : selected.start_ts + 60)
              const pad = Math.max(20, (end - selected.start_ts) * 0.4)
              zoomTo(selected.start_ts - pad, end + pad)
            }} />
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontFamily: MONO, fontSize: 11, color: C.muted }}>
        {['drag across the chart to zoom in', '← → pan', 'L live', 'click a run to inspect and name it', 'dashed runs: end not seen'].map(t => (
          <span key={t} style={{ padding: '5px 9px', border: `1px solid ${C.line}`, borderRadius: 8 }}>{t}</span>
        ))}
      </div>
    </div>
  )
}
