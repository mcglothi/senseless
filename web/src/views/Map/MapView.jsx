import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { Button, Empty, Panel, TextInput, toast } from '../../ui/primitives'
import { guessIcon, inRoom, loadsIndex, markerState, rollups } from '../../ui/floorplan'
import { busyTyping } from '../../api/api'
import { useFloors, IMAGE_TYPES } from '../../hooks/useFloors'
import { Icon } from './icons'
import { MapCanvas, PLAN_STYLES, fmtWatts, fmtWh } from './MapCanvas'
import { DeviceList, FloorSettings, MarkerInspector, Palette, RoomInspector, groupByRoom } from './MapPanel'

const HIDDEN_SENSE = new Set(['Always On', 'Other'])
const lsGet = k => { try { return localStorage.getItem(k) } catch { return null } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* private mode */ } }

// Floor plans: your house with every placed device live on it. View mode is for looking (pan, zoom,
// tap for details); Edit mode is for arranging (palette, drag, rooms, image).
export function MapView({ live, loads, light }) {
  const { isMobile, sp } = useTheme()
  const [planPref, setPlanPref] = useState(() => lsGet('senseless.map.plan'))
  const planStyle = PLAN_STYLES.includes(planPref) ? planPref : light ? 'original' : 'dimmed'
  const cyclePlan = () => { const n = PLAN_STYLES[(PLAN_STYLES.indexOf(planStyle) + 1) % PLAN_STYLES.length]; setPlanPref(n); lsSet('senseless.map.plan', n) }
  const fp = useFloors()
  const [floorId, setFloorId] = useState(() => Number(lsGet('senseless.map.floor')) || null)
  const [edit, setEdit] = useState(false)
  const [tool, setTool] = useState('select')         // select | room
  const [placing, setPlacing] = useState(null)       // palette item armed for tap-to-place
  const [selected, setSelected] = useState(null)     // {type: 'marker'|'room', id}
  const canvasApi = useRef(null)
  const [listView, setListView] = useState(() => lsGet('senseless.map.list') === '1')

  const floors = fp.floors ?? []
  const floor = floors.find(f => f.id === floorId) ?? floors[0]
  useEffect(() => { if (floor) lsSet('senseless.map.floor', String(floor.id)) }, [floor?.id])
  useEffect(() => { lsSet('senseless.map.list', listView ? '1' : '0') }, [listView])

  // Live state
  const loadsByName = useMemo(() => loadsIndex(loads.patterns ?? []), [loads.patterns])
  const senseDevices = useMemo(() => (live.latest?.devices ?? []).filter(d => !HIDDEN_SENSE.has(d.name)), [live.latest?.devices])
  const ctx = { loadsByName, senseDevices }
  const stateOf = m => markerState(m, ctx)
  const nameOf = m => m.label || (m.kind === 'load' ? loadsByName.get(m.ref?.trim().toLowerCase())?.name ?? m.ref : m.kind === 'sense' ? m.ref : 'Label')
  const loadNames = useMemo(() => [...new Set([...loadsByName.values()].map(g => g.name))].sort((a, b) => a.localeCompare(b)), [loadsByName])
  const placedRefs = useMemo(() => new Set(floors.flatMap(f => f.markers.map(m => `${m.kind}:${m.ref}`))), [floors])

  // Selection and edit state reset on floor / mode changes; Escape backs out one step.
  useEffect(() => { setSelected(null); setPlacing(null) }, [floor?.id])
  useEffect(() => { if (!edit) { setTool('select'); setPlacing(null) } }, [edit])
  useEffect(() => {
    const onKey = e => {
      if (busyTyping()) return
      if (e.key === 'Escape') { if (placing) setPlacing(null); else if (tool === 'room') setTool('select'); else setSelected(null) }
      if (e.key === 'e' && !e.metaKey && !e.ctrlKey && !e.altKey && floor) setEdit(v => !v)
      if (e.key === 'r' && edit && !e.metaKey && !e.ctrlKey && !e.altKey) setTool(t => (t === 'room' ? 'select' : 'room'))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [placing, tool, edit, floor])

  // Actions
  const onPlace = useCallback(async (p, item = placing) => {
    if (!item || !floor) return
    const body = { kind: item.kind, x: p.x, y: p.y, icon: item.icon ?? (item.kind === 'label' ? null : guessIcon(item.title)) }
    if (item.ref != null) body.ref = item.ref
    if (item.label != null) body.label = item.label
    const m = await fp.addMarker(floor.id, body)
    setPlacing(null)
    setSelected({ type: 'marker', id: m.id })
  }, [placing, floor, fp])
  const onMoveMarker = useCallback((id, b) => fp.patchMarker(id, b).catch(() => {}), [fp])
  const onDeleteMarker = useCallback(id => { fp.deleteMarker(id).catch(() => {}); setSelected(null) }, [fp])
  const onDrawRoom = useCallback(async r => {
    const n = (floor?.rooms.length ?? 0) + 1
    const room = await fp.addRoom(floor.id, { name: `Room ${n}`, ...r })
    setTool('select')
    setSelected({ type: 'room', id: room.id })
  }, [floor, fp])
  const onPatchRoom = useCallback((id, b) => fp.patchRoom(id, b).catch(() => {}), [fp])
  const upload = async file => {
    if (!IMAGE_TYPES.includes(file.type)) { toast('Use a PNG, JPEG or WebP image'); return }
    toast('Uploading…')
    await fp.uploadImage(floor.id, file).then(() => toast('Floor plan saved')).catch(() => {})
  }
  const addFloor = async name => {
    const f = await fp.addFloor(name)
    setFloorId(f.id)
    setEdit(true)
  }

  if (fp.floors == null) return fp.error ? <Empty title="Couldn't load floor plans">{fp.error}</Empty> : <Empty title="Loading…" />
  if (!floor) return <Onboarding onCreate={addFloor} />

  const roll = rollups(floor, stateOf)
  const selMarker = selected?.type === 'marker' && floor.markers.find(m => m.id === selected.id)
  const selRoom = selected?.type === 'room' && roll.rooms.find(r => r.id === selected.id)
  const height = isMobile ? 'calc(100svh - 230px)' : 'calc(100vh - 210px)'

  let panel = null
  if (selMarker) {
    panel = <MarkerInspector m={selMarker} name={nameOf(selMarker)} state={stateOf(selMarker)} edit={edit} loadNames={loadNames} senseDevices={senseDevices}
      room={roll.rooms.find(r => inRoom(selMarker, r))}
      onPatch={b => fp.patchMarker(selMarker.id, b).catch(() => {})} onDelete={() => onDeleteMarker(selMarker.id)} onClose={() => setSelected(null)} />
  } else if (selRoom) {
    panel = <RoomInspector room={selRoom} devices={floor.markers.filter(m => inRoom(m, selRoom))} edit={edit} nameOf={nameOf} stateOf={stateOf}
      onPatch={b => fp.patchRoom(selRoom.id, b).catch(() => {})} onDelete={() => { fp.deleteRoom(selRoom.id).catch(() => {}); setSelected(null) }}
      onClose={() => setSelected(null)} onPick={m => setSelected({ type: 'marker', id: m.id })} />
  } else if (edit) {
    panel = (
      <>
        <Palette loadNames={loadNames} senseDevices={senseDevices} placedRefs={placedRefs} placing={placing} onArm={setPlacing}
          onPlaceNow={it => { const c = canvasApi.current?.center(); if (c) onPlace(c, it) }} />
        <div style={{ height: 1, background: C.line, margin: '18px 0 14px' }} />
        <FloorSettings floor={floor} count={floor.rooms.length + floor.markers.length}
          onRename={name => fp.patchFloor(floor.id, { name }).catch(() => {})} onReplaceImage={upload}
          onDelete={() => { fp.deleteFloor(floor.id).then(() => { setFloorId(null); setEdit(false) }).catch(() => {}) }} />
      </>
    )
  }

  const canvas = (
    <MapCanvas floor={floor} rollup={roll} stateOf={stateOf} nameOf={nameOf} edit={edit} tool={tool} placing={placing}
      selected={selected} onSelect={setSelected} onPlace={onPlace} onMoveMarker={onMoveMarker} onDeleteMarker={onDeleteMarker}
      onDrawRoom={onDrawRoom} onPatchRoom={onPatchRoom} onDropFile={edit ? upload : null} height={height} planStyle={planStyle} onCyclePlan={cyclePlan} apiRef={canvasApi}>
      {edit && <EditBar tool={tool} setTool={setTool} placing={placing} onCancelPlace={() => setPlacing(null)} />}
      {!floor.image_url && !floor.markers.length && !floor.rooms.length && <UploadPrompt edit={edit} onFile={upload} onEdit={() => setEdit(true)} />}
      {!edit && floor.markers.length > 0 && <Legend />}
    </MapCanvas>
  )

  return (
    <div>
      <style>{`
        @keyframes sl-breathe { 0%, 100% { transform: scale(1) } 50% { transform: scale(1.07) } }
        .sl-breathe { animation: sl-breathe 3.2s ease-in-out infinite }
        @media (prefers-reduced-motion: reduce) { .sl-breathe { animation: none } }
      `}</style>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: sp(12), flexWrap: 'wrap' }}>
        <FloorSwitcher floors={floors} current={floor.id} onPick={setFloorId} watts={f => rollups(f, stateOf).watts} edit={edit} onAdd={addFloor} />
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>
          {roll.on} on · <span style={{ color: C.bright }}>{fmtWatts(roll.watts)}</span> mapped
        </span>
        <Segmented value={listView ? 'list' : 'map'} onChange={v => setListView(v === 'list')} options={[['map', 'Map'], ['list', 'List']]} label="View" />
        <Button variant={edit ? 'primary' : 'default'} onClick={() => setEdit(v => !v)} aria-pressed={edit} title="Edit (E)">
          {edit ? 'Done' : 'Edit'}
        </Button>
      </div>

      {listView
        ? <ListView floors={floors} stateOf={stateOf} nameOf={nameOf} onPick={(f, sel) => { setFloorId(f.id); setListView(false); setTimeout(() => setSelected(sel), 0) }} />
        : isMobile
          ? <>
              {canvas}
              {panel && <BottomSheet onClose={() => { setSelected(null); if (!selected) setEdit(false) }}>{panel}</BottomSheet>}
            </>
          : <div style={{ display: 'grid', gridTemplateColumns: panel ? 'minmax(0, 1fr) 320px' : 'minmax(0, 1fr)', gap: sp(14), alignItems: 'start' }}>
              {canvas}
              {panel && <Panel style={{ padding: 16, maxHeight: height, overflowY: 'auto' }}>{panel}</Panel>}
            </div>}
    </div>
  )
}

function Segmented({ value, onChange, options, label }) {
  const { accent } = useTheme()
  return (
    <div role="radiogroup" aria-label={label} style={{ display: 'inline-flex', border: `1px solid ${C.line2}`, borderRadius: 8, overflow: 'hidden' }}>
      {options.map(([v, t]) => (
        <button key={v} role="radio" aria-checked={value === v} onClick={() => onChange(v)}
          style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', padding: '6px 11px', cursor: 'pointer', border: 'none',
            background: value === v ? alpha(accent, '1c') : 'transparent', color: value === v ? accent : C.muted }}>{t}</button>
      ))}
    </div>
  )
}

function FloorSwitcher({ floors, current, onPick, watts, edit, onAdd }) {
  const { accent } = useTheme()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  return (
    <div role="tablist" aria-label="Floors" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {floors.map(f => {
        const on = f.id === current, w = watts(f)
        return (
          <button key={f.id} role="tab" aria-selected={on} onClick={() => onPick(f.id)}
            style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '7px 12px', borderRadius: 9, cursor: 'pointer',
              border: `1px solid ${on ? alpha(accent, '80') : C.line}`, background: on ? alpha(accent, '14') : C.panel }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: on ? C.bright : C.body }}>{f.name}</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: w > 0 ? (on ? accent : C.muted) : C.dim, fontVariantNumeric: 'tabular-nums' }}>{w > 0 ? fmtWatts(w) : '—'}</span>
          </button>
        )
      })}
      {edit && floors.length < 20 && (adding
        ? <form onSubmit={e => { e.preventDefault(); if (name.trim()) { onAdd(name.trim()); setName(''); setAdding(false) } }} style={{ display: 'flex', gap: 6 }}>
            <TextInput autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="Upstairs" maxLength={60} style={{ width: 140 }}
              onBlur={() => !name && setAdding(false)} aria-label="New floor name" />
            <Button type="submit" variant="primary" size="sm">Add</Button>
          </form>
        : <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>+ Floor</Button>)}
    </div>
  )
}

function EditBar({ tool, setTool, placing, onCancelPlace }) {
  const { accent } = useTheme()
  const hint = placing ? `Tap the map to place ${placing.title.split(' (')[0]}` : tool === 'room' ? 'Drag on the map to draw a room' : 'Drag devices to move them · tap to edit'
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ position: 'absolute', left: 10, top: 10, right: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', pointerEvents: 'none' }}>
      <div style={{ display: 'inline-flex', gap: 2, padding: 3, borderRadius: 9, background: alpha(C.panel, 'ee'), border: `1px solid ${C.line2}`, pointerEvents: 'auto' }}>
        {[['select', 'Select', 'Select and move (Esc)'], ['room', 'Room', 'Draw a room (R)']].map(([v, t, title]) => (
          <button key={v} onClick={() => setTool(v)} aria-pressed={tool === v} title={title}
            style={{ fontFamily: MONO, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', padding: '5px 10px', borderRadius: 6, cursor: 'pointer', border: 'none',
              background: tool === v && !placing ? alpha(accent, '22') : 'transparent', color: tool === v && !placing ? accent : C.muted }}>{t}</button>
        ))}
      </div>
      <span style={{ fontFamily: MONO, fontSize: 11, color: placing ? accent : C.muted, padding: '5px 10px', borderRadius: 8, background: alpha(C.panel, 'dd'), border: `1px solid ${placing ? alpha(accent, '60') : C.line}` }}>
        {hint}
      </span>
      {placing && <button onClick={onCancelPlace} style={{ pointerEvents: 'auto', fontFamily: MONO, fontSize: 11, color: C.muted, background: alpha(C.panel, 'ee'), border: `1px solid ${C.line2}`, borderRadius: 8, padding: '5px 10px', cursor: 'pointer' }}>Cancel</button>}
    </div>
  )
}

function UploadPrompt({ edit, onFile, onEdit }) {
  const { accent } = useTheme()
  const ref = useRef(null)
  const [dismissed, setDismissed] = useState(false)
  if (dismissed) return null
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: 16, background: alpha(C.bg, '99') }}>
      <div style={{ maxWidth: 420, textAlign: 'center', padding: '28px 24px', borderRadius: 14, background: C.panel, border: `1px dashed ${alpha(accent, '70')}` }}>
        <Icon name="panel" size={34} color={accent} style={{ margin: '0 auto 12px' }} />
        <div style={{ fontSize: 18, fontWeight: 700, color: C.bright, marginBottom: 6 }}>Add a floor plan</div>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.7, marginBottom: 16 }}>
          Drop a blueprint, a phone photo of a sketch, or a screenshot from any floor-plan app. PNG, JPEG or WebP, up to 15 MB.
        </div>
        <input ref={ref} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) onFile(f) }} />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
          <Button variant="primary" onClick={() => ref.current.click()}>Choose image</Button>
          <Button variant="ghost" onClick={() => { setDismissed(true); if (!edit) onEdit() }}>Use a blank grid</Button>
        </div>
      </div>
    </div>
  )
}

function Legend() {
  const { accent } = useTheme()
  const dot = (c, ring) => <span style={{ width: 9, height: 9, borderRadius: '50%', background: ring ? 'transparent' : c, border: `1.5px solid ${c}`, display: 'inline-block' }} />
  return (
    <div style={{ position: 'absolute', left: 10, bottom: 10, display: 'flex', gap: 10, padding: '5px 10px', borderRadius: 8, pointerEvents: 'none',
      background: alpha(C.panel, 'dd'), border: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 10, color: C.muted, alignItems: 'center' }}>
      <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}>{dot(C.dim, true)}off</span>
      <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}>{dot(accent)}on</span>
      <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}>{dot(C.orange)}≥ 1 kW</span>
    </div>
  )
}

function BottomSheet({ children, onClose }) {
  return (
    <div role="dialog" aria-modal="false" style={{ position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 40, maxHeight: '58svh', overflowY: 'auto',
      background: C.panel, borderTop: `1px solid ${C.line2}`, borderRadius: '16px 16px 0 0', padding: '8px 16px calc(16px + env(safe-area-inset-bottom))',
      boxShadow: `0 -10px 30px ${alpha(C.bg, 'cc')}` }}>
      <button onClick={onClose} aria-label="Close panel" style={{ display: 'block', margin: '0 auto 10px', width: 44, height: 5, borderRadius: 3, background: C.line2, border: 'none', cursor: 'pointer' }} />
      {children}
    </div>
  )
}

function ListView({ floors, stateOf, nameOf, onPick }) {
  const { accent } = useTheme()
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {floors.map(f => {
        const { groups, rest } = groupByRoom(f)
        const roll = rollups(f, stateOf)
        return (
          <Panel key={f.id} style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
              <span style={{ fontSize: 17, fontWeight: 700, color: C.bright }}>{f.name}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: roll.watts > 0 ? accent : C.dim }}>{roll.watts > 0 ? fmtWatts(roll.watts) : 'idle'} · {roll.on} on</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
              {groups.map(({ room, markers }) => {
                const r = roll.rooms.find(x => x.id === room.id)
                return (
                  <div key={room.id}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.muted, marginBottom: 4 }}>
                      <button onClick={() => onPick(f, { type: 'room', id: room.id })} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', font: 'inherit', letterSpacing: 'inherit', textTransform: 'inherit', color: 'inherit' }}>{room.name}</button><span style={{ color: r.watts > 0 ? C.bright : C.dim }}>{r.watts > 0 ? fmtWatts(r.watts) : ''}{r.wh > 0 ? ` · ${fmtWh(r.wh)}` : ''}</span>
                    </div>
                    <DeviceList devices={markers} nameOf={nameOf} stateOf={stateOf} onPick={m => onPick(f, { type: 'marker', id: m.id })} />
                  </div>
                )
              })}
              {rest.length > 0 && (
                <div>
                  <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', color: C.dim, marginBottom: 4 }}>Not in a room</div>
                  <DeviceList devices={rest} nameOf={nameOf} stateOf={stateOf} onPick={m => onPick(f, { type: 'marker', id: m.id })} />
                </div>
              )}
              {!groups.length && !rest.length && <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>Nothing placed on this floor yet.</div>}
            </div>
          </Panel>
        )
      })}
    </div>
  )
}

function Onboarding({ onCreate }) {
  const { accent } = useTheme()
  const [name, setName] = useState('Main floor')
  return (
    <div style={{ maxWidth: 560, margin: '40px auto', textAlign: 'center' }}>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 10, marginBottom: 18, color: accent }}>
        {['heater', 'fan', 'microwave', 'fridge', 'light'].map((i, k) => <Icon key={i} name={i} size={26} color={k === 2 ? C.orange : k === 0 ? accent : C.dim} />)}
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color: C.bright, marginBottom: 10 }}>Map your home</div>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.muted, lineHeight: 1.75, marginBottom: 22 }}>
        Upload a floor plan, drop your loads where they live and draw the rooms.<br />
        Then watch the space heater light up the bedroom and the microwave light up the kitchen, live.
      </div>
      <form onSubmit={e => { e.preventDefault(); if (name.trim()) onCreate(name.trim()) }} style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
        <TextInput value={name} onChange={e => setName(e.target.value)} maxLength={60} aria-label="Floor name" style={{ width: 200 }} />
        <Button type="submit" variant="primary">Create floor</Button>
      </form>
    </div>
  )
}
