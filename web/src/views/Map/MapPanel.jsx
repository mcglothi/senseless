import { useEffect, useMemo, useRef, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { Button, Label, TextInput } from '../../ui/primitives'
import { ICONS, guessIcon, inRoom, tier } from '../../ui/floorplan'
import { link } from '../../router'
import { Icon } from './icons'
import { DRAG_TYPE, fmtWatts, fmtWh } from './MapCanvas'

const sectionLabel = { fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: 1.5, textTransform: 'uppercase', margin: '14px 0 6px' }

// Edit mode, nothing selected: everything you can put on the map. Drag an item onto the floor, or
// click it and then click the map (the only way on touch screens).
export function Palette({ loadNames, senseDevices, placedRefs, placing, onArm }) {
  const { accent } = useTheme()
  const [q, setQ] = useState('')
  const match = s => s.toLowerCase().includes(q.trim().toLowerCase())
  const items = useMemo(() => [
    ...loadNames.map(n => ({ kind: 'load', ref: n, label: null, icon: guessIcon(n), title: n })),
    ...senseDevices.map(d => ({ kind: 'sense', ref: String(d.id), label: d.name, icon: guessIcon(d.name), title: d.name })),
  ], [loadNames, senseDevices])
  const visible = items.filter(i => match(i.title))
  // unplaced first, then alphabetical
  visible.sort((a, b) => (placedRefs.has(`${a.kind}:${a.ref}`) - placedRefs.has(`${b.kind}:${b.ref}`)) || a.title.localeCompare(b.title))

  const Item = ({ it }) => {
    const placed = placedRefs.has(`${it.kind}:${it.ref}`)
    const armed = placing && placing.kind === it.kind && placing.ref === it.ref
    return (
      <div draggable onDragStart={e => { e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(it)); e.dataTransfer.effectAllowed = 'copy' }}
        onClick={() => onArm(armed ? null : it)} role="button" tabIndex={0}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onArm(armed ? null : it) } }}
        style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 9px', borderRadius: 8, cursor: 'grab',
          border: `1px solid ${armed ? accent : C.line}`, background: armed ? alpha(accent, '18') : C.panel, opacity: placed ? 0.6 : 1 }}>
        <Icon name={it.icon} size={16} color={armed ? accent : C.muted} />
        <span style={{ flex: 1, fontFamily: MONO, fontSize: 12, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.title}</span>
        {placed && <span style={{ fontFamily: MONO, fontSize: 9, color: C.dim }}>placed</span>}
      </div>
    )
  }

  const loads = visible.filter(i => i.kind === 'load'), sense = visible.filter(i => i.kind === 'sense')
  return (
    <div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.6, marginBottom: 10 }}>
        Drag a device onto the floor, or tap it and then tap the map.
      </div>
      <TextInput value={q} onChange={e => setQ(e.target.value)} placeholder="Filter devices…" aria-label="Filter devices" />
      <div style={sectionLabel}>Your named loads</div>
      <div style={{ display: 'grid', gap: 5 }}>
        {loads.map(it => <Item key={`l${it.ref}`} it={it} />)}
        {!loads.length && <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
          {q ? 'No match.' : <>Name loads in <a href="/loads" onClick={link('/loads')} style={{ color: accent }}>Loads</a> to place them here.</>}
        </div>}
      </div>
      {sense.length > 0 && <>
        <div style={sectionLabel}>Sense devices (live)</div>
        <div style={{ display: 'grid', gap: 5 }}>{sense.map(it => <Item key={`s${it.ref}`} it={it} />)}</div>
      </>}
      <div style={sectionLabel}>Other</div>
      <Item it={{ kind: 'label', ref: null, label: 'Label', icon: null, title: 'Text label (panel, router…)' }} />
    </div>
  )
}

function Stat({ label, value, color }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: 1.5, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: color ?? C.bright, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{value}</div>
    </div>
  )
}

function Header({ icon, title, sub, onClose }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
      {icon}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: C.bright, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>
        {sub && <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: 1 }}>{sub}</div>}
      </div>
      <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 'none', color: C.muted, cursor: 'pointer', fontSize: 18, padding: 4 }}>✕</button>
    </div>
  )
}

// A field that saves on blur / Enter, not per keystroke.
function SaveField({ value, onSave, placeholder, label, autoFocus }) {
  const [v, setV] = useState(value ?? '')
  useEffect(() => setV(value ?? ''), [value])
  const commit = () => { if (v.trim() !== (value ?? '')) onSave(v.trim()) }
  return <TextInput value={v} onChange={e => setV(e.target.value)} onBlur={commit} placeholder={placeholder} aria-label={label} autoFocus={autoFocus}
    maxLength={60} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} />
}

function IconPicker({ value, onPick }) {
  const { accent } = useTheme()
  return (
    <div role="radiogroup" aria-label="Icon" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(34px, 1fr))', gap: 4 }}>
      {ICONS.map(i => (
        <button key={i} role="radio" aria-checked={value === i} aria-label={i} title={i} onClick={() => onPick(i)}
          style={{ height: 34, display: 'grid', placeItems: 'center', borderRadius: 7, cursor: 'pointer',
            border: `1px solid ${value === i ? accent : C.line}`, background: value === i ? alpha(accent, '1c') : 'transparent', color: value === i ? accent : C.muted }}>
          <Icon name={i} size={16} />
        </button>
      ))}
    </div>
  )
}

export function MarkerInspector({ m, name, state, room, edit, loadNames, senseDevices, onPatch, onDelete, onClose }) {
  const { accent } = useTheme()
  const t = tier(state)
  const color = t === 3 ? C.orange : t === 2 ? accent : C.muted
  const source = m.kind === 'load' ? 'Detected load' : m.kind === 'sense' ? 'Sense device' : 'Label'
  return (
    <div>
      <Header icon={<div style={{ width: 40, height: 40, borderRadius: '50%', display: 'grid', placeItems: 'center', border: `1.5px solid ${alpha(color, '90')}`, color }}>
        <Icon name={m.icon || 'plug'} size={20} /></div>}
        title={name} sub={`${source}${room ? ` · ${room.name}` : ''}`} onClose={onClose} />
      {m.kind !== 'label' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '12px 14px', borderRadius: 10, background: C.field, border: `1px solid ${C.line}` }}>
          <Stat label="Now" value={state.on ? `${state.estimated ? '≈ ' : ''}${fmtWatts(state.w)}` : state.known ? 'Off' : '—'} color={state.on ? color : C.dim} />
          <Stat label="Today" value={fmtWh(state.whToday)} />
          {!state.known && <div style={{ gridColumn: '1 / -1', fontFamily: MONO, fontSize: 11, color: C.dim, lineHeight: 1.5 }}>
            No detected load is named “{m.ref}” right now. It lights up once a pattern with that name runs.</div>}
          {state.estimated && state.known && <div style={{ gridColumn: '1 / -1', fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.5 }}>
            ≈ is the load's typical step size; today's energy comes from detected runs.</div>}
        </div>
      )}
      {m.kind === 'load' && !edit && (
        <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap' }}>
          <a href="/timeline" onClick={link('/timeline')}><Button size="sm">Timeline</Button></a>
          <a href="/loads" onClick={link('/loads')}><Button size="sm">Loads</Button></a>
        </div>
      )}
      {edit && (
        <div style={{ marginTop: 14, display: 'grid', gap: 12 }}>
          <div>
            <Label>{m.kind === 'label' ? 'Text' : 'Display name (optional)'}</Label>
            <SaveField value={m.label} placeholder={m.kind === 'label' ? 'Label' : m.kind === 'load' ? m.ref : name} label="Display name"
              onSave={v => onPatch({ label: v || (m.kind === 'label' ? 'Label' : null) })} />
          </div>
          {m.kind === 'load' && (
            <div>
              <Label>Tied to load</Label>
              <select value={m.ref} onChange={e => onPatch({ ref: e.target.value })} aria-label="Tied to load"
                style={{ width: '100%', background: C.field, border: `1px solid ${C.line2}`, borderRadius: 8, color: C.text, fontFamily: MONO, fontSize: 12, padding: '8px 11px' }}>
                {[...new Set([m.ref, ...loadNames])].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          )}
          {m.kind === 'sense' && senseDevices.length > 0 && (
            <div>
              <Label>Tied to Sense device</Label>
              <select value={m.ref} onChange={e => onPatch({ ref: e.target.value, label: senseDevices.find(d => String(d.id) === e.target.value)?.name ?? m.label })}
                aria-label="Tied to Sense device"
                style={{ width: '100%', background: C.field, border: `1px solid ${C.line2}`, borderRadius: 8, color: C.text, fontFamily: MONO, fontSize: 12, padding: '8px 11px' }}>
                {!senseDevices.some(d => String(d.id) === m.ref) && <option value={m.ref}>{m.label ?? m.ref} (not live)</option>}
                {senseDevices.map(d => <option key={d.id} value={String(d.id)}>{d.name}</option>)}
              </select>
            </div>
          )}
          <div><Label>Icon</Label><IconPicker value={m.icon} onPick={icon => onPatch({ icon })} /></div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6 }}>Drag to move · arrow keys nudge (shift: more) · Delete removes</div>
          <div><Button variant="danger" onClick={onDelete}>Remove from map</Button></div>
        </div>
      )}
    </div>
  )
}

export function RoomInspector({ room, devices, edit, nameOf, stateOf, onPatch, onDelete, onClose, onPick }) {
  return (
    <div>
      <Header icon={<Icon name="panel" size={22} color={C.muted} />} title={room.name} sub={`${room.count} device${room.count === 1 ? '' : 's'}`} onClose={onClose} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, padding: '12px 14px', borderRadius: 10, background: C.field, border: `1px solid ${C.line}` }}>
        <Stat label="Now" value={room.watts > 0 ? fmtWatts(room.watts) : 'Idle'} color={room.watts > 0 ? undefined : C.dim} />
        <Stat label="Today" value={fmtWh(room.wh || null)} />
      </div>
      <div style={sectionLabel}>Devices</div>
      <DeviceList devices={devices} nameOf={nameOf} stateOf={stateOf} onPick={onPick} />
      {edit && (
        <div style={{ marginTop: 14, display: 'grid', gap: 12 }}>
          <div><Label>Room name</Label><SaveField value={room.name} label="Room name" onSave={v => v && onPatch({ name: v })} /></div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6 }}>Drag the room's name to move it · drag the corner handle to resize</div>
          <div><Button variant="danger" onClick={onDelete}>Delete room</Button></div>
        </div>
      )}
    </div>
  )
}

export function DeviceList({ devices, nameOf, stateOf, onPick }) {
  const { accent } = useTheme()
  if (!devices.length) return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>Nothing placed here yet.</div>
  return (
    <div style={{ display: 'grid', gap: 2 }}>
      {devices.map(m => {
        const s = stateOf(m), t = tier(s)
        const color = t === 3 ? C.orange : t === 2 ? accent : t === 1 ? C.body : C.dim
        return (
          <button key={m.id} onClick={() => onPick(m)} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '6px 4px', background: 'none', border: 'none',
            borderBottom: `1px solid ${C.line}`, cursor: 'pointer', textAlign: 'left', width: '100%' }}>
            <Icon name={m.icon || (m.kind === 'label' ? 'other' : 'plug')} size={15} color={color} />
            <span style={{ flex: 1, fontFamily: MONO, fontSize: 12, color: C.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{nameOf(m)}</span>
            {m.kind !== 'label' && <span style={{ fontFamily: MONO, fontSize: 11, color, fontVariantNumeric: 'tabular-nums' }}>{s.on ? fmtWatts(s.w) : s.known ? 'off' : '—'}</span>}
          </button>
        )
      })}
    </div>
  )
}

export function FloorSettings({ floor, count, onRename, onReplaceImage, onDelete }) {
  const fileRef = useRef(null)
  const [confirm, setConfirm] = useState(false)
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div><Label>Floor name</Label><SaveField value={floor.name} label="Floor name" onSave={v => v && onRename(v)} /></div>
      <div>
        <Label>Floor plan image</Label>
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden
          onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) onReplaceImage(f) }} />
        <Button onClick={() => fileRef.current.click()}>{floor.image_url ? 'Replace image' : 'Upload image'}</Button>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 6, lineHeight: 1.5 }}>
          PNG, JPEG or WebP, up to 15 MB. Devices and rooms keep their places when you replace it.</div>
      </div>
      <div>
        {!confirm
          ? <Button variant="danger" onClick={() => setConfirm(true)}>Delete floor</Button>
          : <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>Delete “{floor.name}” and its {count} item{count === 1 ? '' : 's'}?</span>
              <Button variant="danger" onClick={onDelete}>Delete</Button>
              <Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
            </div>}
      </div>
    </div>
  )
}

// Devices grouped by room, for the list view and room inspectors.
export function groupByRoom(floor) {
  const used = new Set()
  const groups = floor.rooms.map(r => {
    const ms = floor.markers.filter(m => !used.has(m.id) && inRoom(m, r))
    ms.forEach(m => used.add(m.id))
    return { room: r, markers: ms }
  })
  const rest = floor.markers.filter(m => !used.has(m.id))
  return { groups, rest }
}

