import { useEffect, useMemo, useRef, useState } from 'react'
import { C, MONO, alpha, glow, useTheme } from '../../ui/theme'
import { SectionTitle } from '../../ui/primitives'
import { api } from '../../api/api'
import { WINDOW_S } from '../../hooks/useLive'
import { find240V, fmtClock, fmtInt, fmtMoney, fmtSigned, legShare, repeatingSteps } from '../../ui/format'

// The live "Now" view: what the house draws this second, today's totals, the last
// half hour per leg with every step the monitor caught, and what it has spotted.

function Card({ children, style, color }) {
  const { cardRadius, sp } = useTheme()
  return (
    <section style={{
      background: C.panel, border: `1px solid ${color ? alpha(color, '40') : C.line}`, borderRadius: cardRadius + 4,
      padding: `${sp(20)}px ${sp(22)}px`, display: 'flex', flexDirection: 'column', gap: sp(14), minWidth: 0, ...style,
    }}>{children}</section>
  )
}

const Kicker = ({ children, right }) => <SectionTitle right={right} style={{ margin: 0 }}>{children}</SectionTitle>

function Stat({ value, unit, label, color = C.bright }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 32, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
        {value}{unit && <span style={{ fontSize: 14, color: C.muted, fontWeight: 500 }}> {unit}</span>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, color: C.dim, textTransform: 'uppercase' }}>{label}</div>
    </div>
  )
}

function Hero({ latest, series }) {
  const [l1, l2] = latest?.channels ?? []
  const [v1, v2] = latest?.voltage ?? []
  const [p1, p2] = legShare(l1, l2)
  // change vs one minute ago, from the bucketed series
  const minuteAgo = series.length ? series.find(b => b.t >= series[series.length - 1].t - 60) : null
  const delta = latest?.w != null && minuteAgo?.w != null ? latest.w - minuteAgo.w : null
  const heavy = p2 >= 70 ? 2 : p1 >= 70 ? 1 : null
  return (
    <Card style={{ position: 'relative', overflow: 'hidden' }}>
      <Kicker>Drawing now</Kicker>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 'clamp(56px, 8vw, 96px)', fontWeight: 700, lineHeight: 0.9, letterSpacing: -3, color: C.bright, fontVariantNumeric: 'tabular-nums' }}>
          {fmtInt(latest?.w)}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 20, color: C.muted }}>W</span>
        {delta != null && Math.abs(delta) >= 5 && (
          <span style={{ fontFamily: MONO, fontSize: 12, color: delta < 0 ? C.green : C.orange, marginLeft: 6 }}>
            {delta < 0 ? '▼' : '▲'} {fmtInt(Math.abs(delta))} W vs 1 min ago
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div role="img" aria-label={`L1 ${p1} percent, L2 ${p2} percent`} style={{ display: 'flex', height: 14, borderRadius: 7, overflow: 'hidden', background: C.field, border: `1px solid ${C.line}` }}>
          <div style={{ width: `${p1}%`, background: C.l1, boxShadow: `0 0 14px ${glow(C.l1, '70')}`, transition: 'width .6s ease' }} />
          <div style={{ width: `${p2}%`, background: C.l2, boxShadow: `0 0 14px ${glow(C.l2, '70')}`, transition: 'width .6s ease' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', fontFamily: MONO, fontSize: 12, color: C.body }}>
          <span><span style={{ color: C.l1 }}>■</span> L1 {fmtInt(l1)} W · {v1 ? v1.toFixed(1) : '—'} V</span>
          <span>L2 {fmtInt(l2)} W · {v2 ? v2.toFixed(1) : '—'} V <span style={{ color: C.l2 }}>■</span></span>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontFamily: MONO, fontSize: 11 }}>
        {heavy && <span style={{ color: C.amber, padding: '5px 10px', borderRadius: 20, border: `1px solid ${alpha(C.amber, '45')}` }}>L{heavy} carries {heavy === 2 ? p2 : p1}% of the house</span>}
        {latest?.hz && <span style={{ color: C.muted, padding: '5px 10px', borderRadius: 20, border: `1px solid ${C.line2}` }}>{latest.hz.toFixed(2)} Hz</span>}
      </div>
    </Card>
  )
}

function Today({ today }) {
  const hours = today?.hourly_w ?? []
  const max = Math.max(1, ...hours.filter(h => h != null))
  const nowHour = new Date().getHours()
  const partial = today && today.coverage < 0.95
  return (
    <Card>
      <Kicker right={<span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>midnight → now{today?.cost_cents_per_kwh ? ` · ${today.cost_cents_per_kwh}¢/kWh` : ''}</span>}>Today</Kicker>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12 }}>
        <Stat value={today?.kwh != null ? today.kwh.toFixed(1) : '—'} unit="kWh" label="Energy" />
        <Stat value={fmtMoney(today?.cost)} label="Cost so far" />
        <Stat value={today?.peak_w != null ? (today.peak_w / 1000).toFixed(1) : '—'} unit="kW" label="Peak" color={C.orange} />
      </div>
      <div role="img" aria-label="Average power per hour today" style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 88 }}>
        {Array.from({ length: 24 }, (_, i) => {
          const v = hours[i]
          const future = i > nowHour
          return (
            <div key={i} title={v != null ? `${i}:00 · ${fmtInt(v)} W avg` : future ? '' : `${i}:00 · no data`} style={{
              flex: 1, borderRadius: '3px 3px 1px 1px',
              height: v != null ? Math.max(4, Math.round((v / max) * 84)) : future ? 2 : 4,
              background: v == null ? (future ? 'transparent' : alpha(C.text, '10'))
                : i === nowHour ? C.accent : v > max * 0.75 ? alpha(C.orange, 'bb') : alpha(C.text, '30'),
              borderTop: future ? `1px dashed ${C.line}` : 'none',
            }} />
          )
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 10, color: C.faint }}>
        <span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>12a</span>
      </div>
      {partial && <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>Collected {Math.round(today.coverage * 100)}% of today so far; totals count only covered time.</div>}
    </Card>
  )
}

// Stacked per-leg area for the last half hour, total line on top, step ticks below.
function LiveChart({ series, deltas }) {
  const ref = useRef(null)
  const [width, setWidth] = useState(900)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.round(e.contentRect.width))))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  const H = 200, LANE = 22, PAD_R = 44
  const W = width - PAD_R
  const now = series.length ? series[series.length - 1].t + 5 : Date.now() / 1000
  const t0 = now - WINDOW_S
  const X = t => ((t - t0) / WINDOW_S) * W
  const max = Math.max(500, ...series.map(b => (b.l1 ?? 0) + (b.l2 ?? 0))) * 1.15
  const Y = w => H - (w / max) * H
  const pts = series.filter(b => b.l1 != null && b.l2 != null)
  const area = (top, bottom) => pts.length < 2 ? '' :
    'M' + pts.map(b => `${X(b.t).toFixed(1)},${Y(top(b)).toFixed(1)}`).join(' L') +
    ' L' + pts.slice().reverse().map(b => `${X(b.t).toFixed(1)},${Y(bottom(b)).toFixed(1)}`).join(' L') + ' Z'
  const l1Area = area(b => b.l1, () => 0)
  const l2Area = area(b => b.l1 + b.l2, b => b.l1)
  const total = pts.length < 2 ? '' : 'M' + pts.map(b => `${X(b.t).toFixed(1)},${Y(b.l1 + b.l2).toFixed(1)}`).join(' L')
  const step = max > 4000 ? 1000 : max > 1600 ? 500 : 250
  const grid = []
  for (let v = step; v < max; v += step) grid.push(v)
  const ticks = deltas.filter(d => d.t >= t0 && Math.abs(d.w) >= 20)
  const labels = [0, 0.25, 0.5, 0.75, 1].map(f => t0 + f * WINDOW_S)
  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={width} height={H + 2 * LANE + 26} role="img" aria-label="Per-leg power over the last 30 minutes with step events" style={{ display: 'block' }}>
        {grid.map(v => (
          <g key={v}>
            <line x1="0" x2={W} y1={Y(v)} y2={Y(v)} stroke={C.line} strokeDasharray="3 5" />
            <text x={W + 6} y={Y(v) + 3} fontSize="10" fontFamily="JetBrains Mono, monospace" fill={C.dim}>{v >= 1000 ? `${v / 1000} kW` : `${v} W`}</text>
          </g>
        ))}
        <path d={l1Area} fill={alpha(C.l1, '40')} stroke={C.l1} strokeWidth="1" />
        <path d={l2Area} fill={alpha(C.l2, '38')} />
        <path d={total} fill="none" stroke={C.text} strokeWidth="1.6" />
        {[1, 2].map(leg => {
          const y = H + 10 + (leg - 1) * LANE
          return (
            <g key={leg}>
              <line x1="0" x2={W} y1={y + 8} y2={y + 8} stroke={C.line} />
              <text x={W + 6} y={y + 11} fontSize="10" fontFamily="JetBrains Mono, monospace" fill={leg === 1 ? C.l1 : C.l2}>L{leg}</text>
              {ticks.filter(d => d.leg === leg).map((d, i) => {
                const up = d.w > 0, c = leg === 1 ? C.l1 : C.l2, h = Math.min(8, 3 + Math.log10(Math.abs(d.w)) * 1.8)
                return <line key={i} x1={X(d.t)} x2={X(d.t)} y1={up ? y + 8 - h : y + 8} y2={up ? y + 8 : y + 8 + h}
                  stroke={up ? c : alpha(c, '80')} strokeWidth="2" strokeLinecap="round" />
              })}
            </g>
          )
        })}
        {labels.map((t, i) => (
          <text key={i} x={Math.min(W - 18, Math.max(18, X(t)))} y={H + 2 * LANE + 22} textAnchor="middle" fontSize="10" fontFamily="JetBrains Mono, monospace" fill={C.faint}>
            {fmtClock(t, { seconds: false })}
          </text>
        ))}
      </svg>
    </div>
  )
}

function Feed({ deltas, pairs, cyclers }) {
  const rows = deltas.filter(d => Math.abs(d.w) >= 20).slice(-9).reverse()
  const pairFrames = new Set(pairs.map(p => p.frame))
  const tagOf = d => {
    if (pairFrames.has(d.frame)) return { text: '240 V appliance', color: C.violet }
    const c = cyclers.find(x => x.leg === d.leg && Math.abs(Math.abs(d.w) - x.w) <= 6)
    if (c) return { text: `≈${c.w} W repeater (${c.count}× today)`, color: C.muted }
    return { text: 'unlabeled', color: C.dim }
  }
  return (
    <Card>
      <Kicker right={<span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>on-board step detections</span>}>Just now</Kicker>
      {rows.length === 0 && <div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>Waiting for the first step…</div>}
      <div role="log" aria-label="Recent steps" style={{ display: 'flex', flexDirection: 'column' }}>
        {rows.map((d, i) => {
          const c = d.leg === 1 ? C.l1 : C.l2, tag = tagOf(d)
          return (
            <div key={`${d.frame}-${d.leg}-${i}`} style={{
              display: 'grid', gridTemplateColumns: '64px 34px 76px 1fr', gap: 10, alignItems: 'center', padding: '7px 0',
              borderBottom: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 12,
            }}>
              <span style={{ color: C.dim }}>{fmtClock(d.t)}</span>
              <span style={{ color: c, border: `1px solid ${alpha(c, '55')}`, borderRadius: 6, textAlign: 'center', fontSize: 10, padding: '2px 0' }}>L{d.leg}</span>
              <span style={{ color: d.w > 0 ? C.bright : C.muted, textAlign: 'right', fontWeight: 700 }}>{fmtSigned(d.w)}</span>
              <span style={{ color: tag.color, fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{tag.text}</span>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function Spotted({ dayDeltas, pairs, cyclers }) {
  const n1 = dayDeltas.filter(d => d.leg === 1).length, n2 = dayDeltas.length - n1
  const Row = ({ color, big, title, sub }) => (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '12px 14px', borderRadius: 12, background: C.field, border: `1px solid ${alpha(color, '45')}` }}>
      <span style={{ fontFamily: MONO, fontSize: 18, fontWeight: 700, color, minWidth: 52 }}>{big}</span>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <span style={{ fontWeight: 600 }}>{title}</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{sub}</span>
      </span>
    </div>
  )
  const ons = pairs.filter(p => p.w > 0)
  const last240 = ons[ons.length - 1] ?? pairs[pairs.length - 1]
  return (
    <Card>
      <Kicker>Spotted today</Kicker>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 40, fontWeight: 700, color: C.bright }}>{fmtInt(dayDeltas.length)}</span>
        <span style={{ color: C.muted, fontSize: 14 }}>steps · {fmtInt(n1)} L1 · {fmtInt(n2)} L2</span>
      </div>
      {pairs.length > 0 && (
        <Row color={C.violet} big={`${ons.length}×`} title="240 V appliance"
          sub={`both legs in the same cycle · last on ${fmtSigned(last240.l1)} / ${fmtSigned(last240.l2)} at ${fmtClock(last240.t, { seconds: false })}`} />
      )}
      {cyclers.slice(0, 2).map(c => (
        <Row key={`${c.leg}-${c.w}`} color={c.leg === 1 ? C.l1 : C.l2} big={`${c.count}×`} title={`${c.w} W repeater on L${c.leg}`} sub="same-size step, over and over · unlabeled" />
      ))}
      {pairs.length === 0 && cyclers.length === 0 && <div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>Nothing repeating yet today.</div>}
    </Card>
  )
}

function AlwaysOn({ latest, series, today }) {
  const ao = latest?.devices?.find(d => d.name === 'Always On')?.w
  const cents = today?.cost_cents_per_kwh
  const perDay = ao != null && cents ? (ao * 24 / 1000) * cents / 100 : null
  // rough per-leg split: the ratio of each leg's lowest point in the window, applied to the baseline
  const legs = series.filter(b => b.l1 != null)
  const share = legs.length ? legShare(Math.min(...legs.map(b => b.l1)), Math.min(...legs.map(b => b.l2))) : null
  const min1 = share && ao != null ? (ao * share[0]) / 100 : null
  const min2 = share && ao != null ? (ao * share[1]) / 100 : null
  return (
    <Card>
      <Kicker>Always on</Kicker>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 40, fontWeight: 700, color: C.bright }}>{fmtInt(ao)}</span>
        <span style={{ color: C.muted, fontSize: 14 }}>W baseline{perDay != null ? ` · ${fmtMoney(perDay)}/day · $${fmtInt(perDay * 365)}/yr` : ''}</span>
      </div>
      {share && min1 != null && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontFamily: MONO, fontSize: 11, color: C.body }}>
          {[[1, min1, share[0]], [2, min2, share[1]]].map(([leg, w, pct]) => (
            <div key={leg}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}><span>L{leg}</span><span>≈{fmtInt(w)} W</span></div>
              <div style={{ height: 6, borderRadius: 3, background: C.field }}>
                <div style={{ width: `${pct}%`, height: 6, borderRadius: 3, background: leg === 1 ? C.l1 : C.l2 }} />
              </div>
            </div>
          ))}
        </div>
      )}
      <p style={{ color: C.muted, fontSize: 13, lineHeight: 1.5 }}>
        Sense's always-on estimate, split by the ratio of each leg's lowest point in the last 30 minutes.
        {share && share[1] >= 70 ? ' Most of it sits on L2: a breaker hunt there would find it.' : share && share[0] >= 70 ? ' Most of it sits on L1.' : ''}
      </p>
    </Card>
  )
}

// Today's steps, for the "spotted" counts; refreshed every 5 minutes and topped up live.
function useDayDeltas(liveDeltas) {
  const [base, setBase] = useState([])
  useEffect(() => {
    let alive = true
    const load = () => {
      const midnight = new Date(); midnight.setHours(0, 0, 0, 0)
      const minutes = Math.ceil((Date.now() - midnight.getTime()) / 60000) + 1
      api('GET', `/api/deltas?minutes=${minutes}`)
        .then(d => { if (alive) setBase(d.map(x => ({ t: x.ts, leg: x.channel + 1, w: x.w, frame: x.start_frame }))) })
        .catch(() => {})
    }
    load()
    const id = setInterval(load, 300000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  return useMemo(() => {
    const midnight = new Date(); midnight.setHours(0, 0, 0, 0)
    const since = midnight.getTime() / 1000  // a tab left open overnight must drop yesterday
    const seen = new Set(base.map(d => `${d.frame}:${d.leg}:${d.w}`))
    return base.concat(liveDeltas.filter(d => !seen.has(`${d.frame}:${d.leg}:${d.w}`))).filter(d => d.t >= since)
  }, [base, liveDeltas])
}

export function NowView({ live, today }) {
  const { sp } = useTheme()
  const { latest, series, deltas } = live
  const dayDeltas = useDayDeltas(deltas)
  const pairs = useMemo(() => find240V(dayDeltas), [dayDeltas])
  const cyclers = useMemo(() => repeatingSteps(dayDeltas, { minCount: 20 }), [dayDeltas])
  const grid = min => ({ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, ${min}px), 1fr))`, gap: sp(18) })
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: sp(18) }}>
      <div style={grid(420)}>
        <Hero latest={latest} series={series} />
        <Today today={today} />
      </div>
      <Card>
        <Kicker right={<span style={{ display: 'flex', gap: 14, fontFamily: MONO, fontSize: 11, color: C.muted }}>
          <span><span style={{ color: C.l1 }}>■</span> L1</span><span><span style={{ color: C.l2 }}>■</span> L2</span><span>— total</span><span>│ step</span>
        </span>}>Last 30 minutes</Kicker>
        <LiveChart series={series} deltas={deltas} />
      </Card>
      <div style={grid(340)}>
        <Feed deltas={deltas} pairs={pairs} cyclers={cyclers} />
        <Spotted dayDeltas={dayDeltas} pairs={pairs} cyclers={cyclers} />
        <AlwaysOn latest={latest} series={series} today={today} />
      </div>
    </div>
  )
}
