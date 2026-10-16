import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { C, MONO, alpha, glow, useTheme } from '../../ui/theme'
import { fitView, rectFrom, tier, toNormalized, zoomAt } from '../../ui/floorplan'
import { fmtInt } from '../../ui/format'
import { Icon } from './icons'

// A floor without an image is a blank grid of this size (same aspect for every screen).
export const BLANK = { w: 1600, h: 1000 }
export const DRAG_TYPE = 'application/x-senseless-marker'
const MIN_ROOM = 0.02
// How the blueprint is drawn under the devices; inverted suits line drawings on a dark theme.
export const PLAN_STYLES = ['original', 'dimmed', 'inverted']
const PLAN_FILTERS = { original: 'none', dimmed: 'brightness(.62) contrast(1.05) saturate(.7)', inverted: 'invert(.92) hue-rotate(180deg) contrast(.9) saturate(.6)' }

export const fmtWatts = w => (w >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${fmtInt(w)} W`)
export const fmtWh = wh => (wh == null ? '—' : wh >= 1000 ? `${(wh / 1000).toFixed(2)} kWh` : `${fmtInt(wh)} Wh`)

// The pannable, zoomable floor. Content is laid out in image pixels inside one transformed layer;
// markers and labels are counter-scaled so they stay the same size at every zoom.
//
// Gestures (one at a time, tracked in `gesture`): pan (background), pinch (two pointers),
// marker move, room draw / move / resize. Window listeners finish a gesture even if the pointer
// leaves the map.
export function MapCanvas({ floor, rollup, stateOf, nameOf, edit, tool, placing, selected, onSelect, onPlace,
  onMoveMarker, onDeleteMarker, onDrawRoom, onPatchRoom, onDropFile, height, planStyle = 'original', onCyclePlan, apiRef, children }) {
  const { accent } = useTheme()
  const boxRef = useRef(null), layerRef = useRef(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 })
  const [draft, setDraftState] = useState(null)      // live geometry during a drag: {marker:{id,x,y}} | {room:{...}}
  const draftRef = useRef(null)
  const setDraft = d => { draftRef.current = d; setDraftState(d) }
  const gesture = useRef(null)
  const pointers = useRef(new Map())
  const viewRef = useRef(view)
  viewRef.current = view
  const iw = floor.img_w || BLANK.w, ih = floor.img_h || BLANK.h

  useLayoutEffect(() => {
    const el = boxRef.current
    const ro = new ResizeObserver(([e]) => setBox({ w: e.contentRect.width, h: e.contentRect.height }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Fit on a new floor or image. Box resizes (the side panel opening) refit only until the user has
  // panned or zoomed; after that they keep their view, re-centred on the same spot.
  const touched = useRef(false)
  const lastBox = useRef(box)
  const fit = () => { touched.current = false; setView(fitView(iw, ih, box.w, box.h, 24)) }
  useEffect(() => { touched.current = false }, [floor.id, iw, ih])
  useEffect(() => {
    const prev = lastBox.current
    lastBox.current = box
    if (!touched.current || !prev.w) setView(fitView(iw, ih, box.w, box.h, 24))
    else setView(v => ({ ...v, x: v.x + (box.w - prev.w) / 2, y: v.y + (box.h - prev.h) / 2 }))
  }, [floor.id, iw, ih, box.w, box.h])
  const userView = f => { touched.current = true; setView(f) }
  // zoom range relative to the fitted view, so a huge plan on a phone can still zoom out to fit
  const fitScale = fitView(iw, ih, box.w, box.h, 24).scale || 1
  const lim = useRef([0.3, 8])
  lim.current = [Math.min(0.3, fitScale * 0.5), Math.max(8, fitScale * 16)]
  const zoom = (v, f, px, py) => zoomAt(v, f, px, py, ...lim.current)

  const norm = (cx, cy) => toNormalized(cx, cy, layerRef.current.getBoundingClientRect())
  // keyboard placement drops at the centre of what's on screen
  if (apiRef) apiRef.current = { center: () => { const b = boxRef.current.getBoundingClientRect(); return norm(b.left + b.width / 2, b.top + b.height / 2) } }
  const local = (cx, cy) => { const r = boxRef.current.getBoundingClientRect(); return { x: cx - r.left, y: cy - r.top } }

  // Wheel zoom around the cursor (needs a non-passive listener to stop page scroll).
  useEffect(() => {
    const el = boxRef.current
    const onWheel = e => {
      e.preventDefault()
      const p = local(e.clientX, e.clientY)
      touched.current = true
      setView(v => zoom(v, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), p.x, p.y))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  useEffect(() => {
    const move = e => {
      if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
      const g = gesture.current
      if (!g) return
      if (g.type !== 'pinch' && e.pointerId !== g.pid) return   // only the pointer that started it
      if (g.type === 'pinch') {
        const [a, b] = [...pointers.current.values()]
        if (!a || !b) return
        const d = Math.hypot(a.x - b.x, a.y - b.y)
        const mid = local((a.x + b.x) / 2, (a.y + b.y) / 2)
        touched.current = true
        setView(v => {
          const z = zoom(v, d / g.d, mid.x, mid.y)
          return { ...z, x: z.x + mid.x - g.mid.x, y: z.y + mid.y - g.mid.y }
        })
        g.d = d; g.mid = mid
        return
      }
      const dx = e.clientX - g.sx, dy = e.clientY - g.sy
      if (Math.hypot(dx, dy) > 4) g.moved = true
      if (g.type === 'pan' && g.moved) { touched.current = true; setView(v => ({ ...v, x: g.vx + dx, y: g.vy + dy })) }
      else if (g.type === 'marker' && g.moved) {
        const p = norm(e.clientX, e.clientY)
        setDraft({ marker: { id: g.id, x: p.x, y: p.y } })
      } else if (g.type === 'draw') {
        setDraft({ room: { id: null, ...rectFrom(g.a, norm(e.clientX, e.clientY)) } })
      } else if (g.type === 'room' && g.moved) {
        const s = viewRef.current.scale
        const ddx = dx / s / iw, ddy = dy / s / ih, r = g.room
        setDraft({ room: g.mode === 'move'
          ? { ...r, x: Math.min(1 - r.w, Math.max(0, r.x + ddx)), y: Math.min(1 - r.h, Math.max(0, r.y + ddy)) }
          : { ...r, w: Math.min(1 - r.x, Math.max(MIN_ROOM, r.w + ddx)), h: Math.min(1 - r.y, Math.max(MIN_ROOM, r.h + ddy)) } })
      }
    }
    const up = e => {
      pointers.current.delete(e.pointerId)
      const g = gesture.current
      if (!g) return
      if (g.type === 'pinch') { if (pointers.current.size < 2) gesture.current = null; return }
      if (e.pointerId !== g.pid) return
      gesture.current = null
      const d = draftRef.current
      setDraft(null)
      if (e.type === 'pointercancel') return             // cancelled: discard, never commit
      {
        if (g.type === 'marker') {
          if (g.moved && d?.marker) onMoveMarker(g.id, { x: d.marker.x, y: d.marker.y })
          else onSelect({ type: 'marker', id: g.id })
        } else if (g.type === 'draw') {
          const r = d?.room
          if (r && r.w >= MIN_ROOM && r.h >= MIN_ROOM) onDrawRoom({ x: r.x, y: r.y, w: r.w, h: r.h })
        } else if (g.type === 'room') {
          if (g.moved && d?.room) onPatchRoom(g.room.id, { x: d.room.x, y: d.room.y, w: d.room.w, h: d.room.h })
          else onSelect({ type: 'room', id: g.room.id })
        } else if (g.type === 'pan' && !g.moved) onSelect(g.target ?? null)
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up) }
  }, [iw, ih, onMoveMarker, onSelect, onDrawRoom, onPatchRoom])

  const start = (e, g) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (gesture.current?.type === 'pinch' || pointers.current.size > 2) return   // extra fingers during a pinch start nothing
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      gesture.current = { type: 'pinch', d: Math.hypot(a.x - b.x, a.y - b.y), mid: local((a.x + b.x) / 2, (a.y + b.y) / 2) }
      setDraft(null)
      return
    }
    gesture.current = { sx: e.clientX, sy: e.clientY, moved: false, pid: e.pointerId, ...g }
  }

  const onBackgroundDown = e => {
    if (edit && placing) {
      const p = norm(e.clientX, e.clientY)
      if (p.inside) onPlace(p)
      return
    }
    if (edit && tool === 'room' && pointers.current.size === 0) {
      const p = norm(e.clientX, e.clientY)
      if (p.inside) { start(e, { type: 'draw', a: p }); return }
    }
    start(e, { type: 'pan', vx: view.x, vy: view.y })
  }

  const onDrop = e => {
    e.preventDefault()
    const file = e.dataTransfer.files?.[0]
    if (file) { onDropFile?.(file); return }
    const raw = e.dataTransfer.getData(DRAG_TYPE)
    if (!raw || !edit) return
    const p = norm(e.clientX, e.clientY)
    if (p.inside) onPlace(p, JSON.parse(raw))
  }

  const markers = floor.markers.map(m => (draft?.marker?.id === m.id ? { ...m, ...draft.marker } : m))
  const rooms = rollup.rooms.map(r => (draft?.room?.id === r.id ? { ...r, ...draft.room } : r))
    .concat(draft?.room && draft.room.id == null ? [{ ...draft.room, watts: 0, wh: 0, count: 0 }] : [])
  const inv = 1 / view.scale
  const dense = view.scale * iw < 600     // whole floor small on screen: only active/selected devices show names
  const cursor = edit && placing ? 'copy' : edit && tool === 'room' ? 'crosshair' : 'grab'

  return (
    <div ref={boxRef} onPointerDown={onBackgroundDown} onDragOver={e => e.preventDefault()} onDrop={onDrop}
      style={{ position: 'relative', height, overflow: 'hidden', touchAction: 'none', cursor, userSelect: 'none',
        background: C.field, borderRadius: 12, border: `1px solid ${C.line}`,
        backgroundImage: `radial-gradient(${alpha(C.faint, '90')} 1px, transparent 1px)`, backgroundSize: '22px 22px' }}>
      <div ref={layerRef} style={{ position: 'absolute', left: 0, top: 0, width: iw, height: ih, transformOrigin: '0 0',
        transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
        {floor.image_url
          ? <img src={floor.image_url} alt={`${floor.name} floor plan`} draggable={false}
              style={{ width: iw, height: ih, display: 'block', borderRadius: 4, filter: PLAN_FILTERS[planStyle], transition: 'filter .3s', pointerEvents: 'none' }} />
          : <div style={{ width: iw, height: ih, border: `2px dashed ${C.line2}`, borderRadius: 8 }} />}

        <svg width={iw} height={ih} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} aria-hidden="true">
          {rooms.map(r => {
            const sel = selected?.type === 'room' && selected.id === r.id
            const heat = r.watts > 0 ? Math.min(0.24, 0.05 + r.watts / 9000) : 0
            return <rect key={r.id} x={r.x * iw} y={r.y * ih} width={r.w * iw} height={r.h * ih} rx={6 * inv}
              fill={heat ? alpha(r.watts >= 1000 ? C.orange : accent, heat) : alpha(C.panel, 0.08)}
              stroke={sel ? accent : alpha(accent, r.watts > 0 ? '70' : '35')} strokeWidth={(sel ? 2 : 1.2) * inv}
              strokeDasharray={r.id == null ? `${6 * inv} ${4 * inv}` : undefined}
              style={{ transition: 'fill .6s' }} />
          })}
        </svg>

        {rooms.filter(r => r.id != null).map(r => (
          <RoomLabel key={r.id} r={r} iw={iw} ih={ih} inv={inv} dense={dense} edit={edit && !placing} accent={accent}
            selected={selected?.type === 'room' && selected.id === r.id}
            onDown={(e, mode) => {
              e.stopPropagation()
              if (edit && placing) { onBackgroundDown(e); return }
              if (!edit) { start(e, { type: 'pan', vx: view.x, vy: view.y, target: { type: 'room', id: r.id } }); return }
              start(e, { type: 'room', mode, room: { id: r.id, x: r.x, y: r.y, w: r.w, h: r.h } })
            }}
            onKey={e => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect({ type: 'room', id: r.id }); return }
              if (!edit) return
              const step = e.shiftKey ? 0.02 : 0.005
              const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
              if (d) {
                e.preventDefault()
                // arrows move; with Alt they resize
                onPatchRoom(r.id, e.altKey
                  ? { w: Math.min(1 - r.x, Math.max(MIN_ROOM, r.w + d[0])), h: Math.min(1 - r.y, Math.max(MIN_ROOM, r.h + d[1])) }
                  : { x: Math.min(1 - r.w, Math.max(0, r.x + d[0])), y: Math.min(1 - r.h, Math.max(0, r.y + d[1])) })
              }
            }} />
        ))}

        {markers.map(m => (
          <Marker key={m.id} m={m} name={nameOf(m)} state={stateOf(m)} iw={iw} ih={ih} inv={inv} dense={dense} accent={accent} edit={edit}
            selected={selected?.type === 'marker' && selected.id === m.id}
            onDown={e => {
              e.stopPropagation()
              if (edit && placing) return
              if (!edit) { start(e, { type: 'pan', vx: view.x, vy: view.y, target: { type: 'marker', id: m.id } }); return }
              start(e, { type: 'marker', id: m.id })
            }}
            onKey={e => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect({ type: 'marker', id: m.id }) }
              if (!edit) return
              const step = e.shiftKey ? 0.02 : 0.004
              const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
              if (d) { e.preventDefault(); onMoveMarker(m.id, { x: Math.min(1, Math.max(0, m.x + d[0])), y: Math.min(1, Math.max(0, m.y + d[1])) }) }
              if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); onDeleteMarker(m.id) }
            }} />
        ))}
      </div>

      <div style={{ position: 'absolute', right: 10, bottom: 10, display: 'flex', flexDirection: 'column', gap: 4 }}
        onPointerDown={e => e.stopPropagation()}>
        {[['+', 'Zoom in', () => userView(v => zoom(v, 1.4, box.w / 2, box.h / 2))],
          ['−', 'Zoom out', () => userView(v => zoom(v, 1 / 1.4, box.w / 2, box.h / 2))],
          ['⤢', 'Fit to screen', fit],
          ...(floor.image_url && onCyclePlan ? [['◐', `Plan: ${planStyle} (click to change)`, onCyclePlan]] : [])].map(([t, label, fn]) => (
          <button key={label} onClick={fn} aria-label={label} title={label} style={{ width: 32, height: 32, borderRadius: 8, cursor: 'pointer',
            background: alpha(C.panel, 'ee'), border: `1px solid ${C.line2}`, color: C.body, fontFamily: MONO, fontSize: 15 }}>{t}</button>
        ))}
      </div>
      {children}
    </div>
  )
}

function RoomLabel({ r, iw, ih, inv, dense, edit, selected, accent, onDown, onKey }) {
  return (
    <>
      <div onPointerDown={e => onDown(e, 'move')} onKeyDown={onKey} role="button" tabIndex={0}
        aria-label={`Room ${r.name}${edit ? ' (arrows move, Alt+arrows resize)' : ''}`}
        style={{ position: 'absolute', left: r.x * iw, top: r.y * ih, transformOrigin: '0 0', transform: `scale(${inv})`,
          padding: dense ? '3px 4px' : '5px 8px', cursor: edit ? 'move' : 'pointer', maxWidth: Math.max(80, r.w * iw / inv), whiteSpace: 'nowrap' }}>
        <div style={{ display: 'inline-block', padding: '4px 8px', borderRadius: 7, background: alpha(C.panel, 'e8'),
          border: `1px solid ${selected ? accent : alpha(r.watts > 0 ? accent : C.line2, r.watts > 0 ? '60' : 'ff')}`, boxShadow: `0 1px 4px ${alpha(C.bg, '50')}` }}>
        <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', overflow: 'hidden', textOverflow: 'ellipsis',
          color: selected ? accent : C.muted }}>{r.name}</div>
        {r.count > 0 && (
          <div style={{ fontFamily: MONO, fontSize: 12, color: r.watts > 0 ? C.bright : C.dim, fontVariantNumeric: 'tabular-nums' }}>
            {r.watts > 0 ? fmtWatts(r.watts) : 'idle'}{r.wh > 0 && !dense && <span style={{ color: C.dim, fontSize: 10 }}> · {fmtWh(r.wh)} today</span>}
          </div>
        )}
        </div>
      </div>
      {edit && selected && (
        <div onPointerDown={e => onDown(e, 'resize')} aria-label={`Resize ${r.name}`}
          style={{ position: 'absolute', left: (r.x + r.w) * iw, top: (r.y + r.h) * ih, width: 16, height: 16, cursor: 'nwse-resize',
            transform: `translate(-50%, -50%) scale(${inv})`, background: accent, borderRadius: 4, border: `2px solid ${C.bg}` }} />
      )}
    </>
  )
}

// One device. Calm states: off = dim; standby = soft; on = accent + glow + watts; heavy (≥ 1 kW) = warm aura that breathes.
function Marker({ m, name, state, iw, ih, inv, dense, edit, selected, accent, onDown, onKey }) {
  const t = tier(state)
  const size = dense ? 28 : 36, showName = !dense || selected || t >= 2
  if (m.kind === 'label') {
    return (
      <div onPointerDown={onDown} onKeyDown={onKey} role="button" tabIndex={0} aria-label={`Label ${name}`}
        style={{ position: 'absolute', left: m.x * iw, top: m.y * ih, transform: `translate(-50%, -50%) scale(${inv})`,
          fontFamily: MONO, fontSize: 11, color: selected ? accent : C.muted, padding: '3px 8px', borderRadius: 6,
          background: alpha(C.panel, 'cc'), border: `1px solid ${selected ? accent : C.line2}`, cursor: edit ? 'grab' : 'pointer', whiteSpace: 'nowrap',
          display: 'flex', alignItems: 'center', gap: 5 }}>
        {m.icon && <Icon name={m.icon} size={12} />}{name}
      </div>
    )
  }
  const color = t === 3 ? C.orange : t === 2 ? accent : t === 1 ? C.body : C.dim
  const label = `${name}: ${state.on ? `on, ${fmtWatts(state.w)}` : state.known ? 'off' : 'not detected yet'}`
  return (
    <div onPointerDown={onDown} onKeyDown={onKey} role="button" tabIndex={0} aria-label={label} aria-pressed={selected} title={label}
      style={{ position: 'absolute', left: m.x * iw, top: m.y * ih, transform: `translate(-50%, -50%) scale(${inv})`,
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, cursor: edit ? 'grab' : 'pointer', outline: 'none' }}>
      <div className={t === 3 ? 'sl-breathe' : undefined} style={{
        width: size, height: size, borderRadius: '50%', display: 'grid', placeItems: 'center', color,
        background: t >= 2 ? `linear-gradient(${alpha(color, '22')}, ${alpha(color, '22')}), ${C.panel}` : C.panel,
        border: `${selected ? 2 : 1.5}px ${state.known ? 'solid' : 'dashed'} ${selected ? accent : t >= 1 ? alpha(color, '90') : C.line2}`,
        boxShadow: t === 3 ? `0 0 0 6px ${alpha(C.orange, '18')}, 0 0 26px ${glow(C.orange, '70')}` : t === 2 ? `0 0 16px ${glow(accent, '55')}` : `0 1px 3px ${alpha(C.bg, '80')}`,
        opacity: t === 0 && !selected ? 0.75 : 1, transition: 'box-shadow .5s, color .5s, background .5s',
      }}>
        <Icon name={m.icon || 'plug'} size={dense ? 15 : 18} />
      </div>
      {showName && <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', lineHeight: 1.2, pointerEvents: 'none' }}>
        <span style={{ fontFamily: MONO, fontSize: 10, color: selected ? accent : t >= 2 ? C.text : C.muted, whiteSpace: 'nowrap', maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis',
          padding: '1px 6px', borderRadius: 5, background: alpha(C.panel, 'e0') }}>{name}</span>
        {t >= 1 && (
          <span style={{ fontFamily: MONO, fontSize: 10, fontWeight: 700, color: t === 3 ? C.bg : color, padding: '0 6px', borderRadius: 10, marginTop: 2,
            background: t === 3 ? C.orange : alpha(C.panel, 'dd'), border: t === 3 ? 'none' : `1px solid ${alpha(color, '60')}`, fontVariantNumeric: 'tabular-nums' }}>
            {state.estimated ? '≈' : ''}{fmtWatts(state.w)}
          </span>
        )}
      </div>}
    </div>
  )
}
