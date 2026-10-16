import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { Button, Empty, Panel, TextInput, toast } from '../../ui/primitives'
import { guessIcon, inRoom, loadsIndex, markerState, rollups } from '../../ui/floorplan'
import { busyTyping } from '../../api/api'
import { link, navigate, useLocation } from '../../router'
import { BUILDING_ICONS, buildingRollup, defaultFloor, defaultFootprint, guessBuildingIcon, levelTag, mapPath, parseMapPath, siteAsFloor } from '../../ui/property'
import { useFloors, IMAGE_TYPES } from '../../hooks/useFloors'
import { Icon } from './icons'
import { MapCanvas, PLAN_STYLES, fmtWatts, fmtWh } from './MapCanvas'
import { DeviceList, FloorSettings, MarkerInspector, Palette, RoomInspector, groupByRoom } from './MapPanel'

const HIDDEN_SENSE = new Set(['Always On', 'Other'])
const lsGet = k => { try { return localStorage.getItem(k) } catch { return null } }
const lsSet = (k, v) => { try { localStorage.setItem(k, v) } catch { /* private mode */ } }

// The property map: zoomed out, the whole property with each building's live load (on a site plan, or as
// tiles until you add one); zoom into a building for its floors, rooms and devices. View mode is for
// looking (pan, zoom, tap); Edit mode is for arranging. URLs: /map, /map/b/{building}, /map/b/{b}/f/{floor}.
export function MapView({ live, loads, light }) {
  const { path } = useLocation()
  const { buildingId, floorId } = parseMapPath(path)
  const fp = useFloors()
  const [planPref, setPlanPref] = useState(() => lsGet('senseless.map.plan'))
  const planStyle = PLAN_STYLES.includes(planPref) ? planPref : light ? 'original' : 'dimmed'
  const cyclePlan = () => { const n = PLAN_STYLES[(PLAN_STYLES.indexOf(planStyle) + 1) % PLAN_STYLES.length]; setPlanPref(n); lsSet('senseless.map.plan', n) }

  // Live state, shared by every level of the map
  const loadsByName = useMemo(() => loadsIndex(loads.patterns ?? []), [loads.patterns])
  const senseDevices = useMemo(() => (live.latest?.devices ?? []).filter(d => !HIDDEN_SENSE.has(d.name)), [live.latest?.devices])
  const ctx = { loadsByName, senseDevices }
  const stateOf = m => markerState(m, ctx)
  const nameOf = m => m.label || (m.kind === 'load' ? loadsByName.get(m.ref?.trim().toLowerCase())?.name ?? m.ref : m.kind === 'sense' ? m.ref : 'Label')
  const shared = { fp, stateOf, nameOf, loadsByName, senseDevices, planStyle, cyclePlan }

  const buildings = fp.property?.buildings ?? []
  const building = buildingId != null ? buildings.find(b => b.id === buildingId) : null
  // a building that no longer exists (deleted, or an old link): back to the property
  useEffect(() => { if (fp.property && buildingId != null && !building) navigate('/map', { replace: true }) }, [fp.property, buildingId, building])

  if (fp.property == null) return fp.error ? <Empty title="Couldn't load the map">{fp.error}</Empty> : <Empty title="Loading…" />
  if (!buildings.length) {
    return <Onboarding title="Map your property" text={<>Start with your main building; add the garage, sheds and the rest later.<br />Then upload floor plans and drop your loads where they live.</>}
      initial="Main house" label="Building name" cta="Create building"
      onCreate={async name => { const b = await fp.addBuilding({ name, icon: guessBuildingIcon(name) }); navigate(mapPath(b.id)) }} />
  }
  if (buildingId == null) return <PropertyView {...shared} property={fp.property} />
  if (!building) return <Empty title="Loading…" />
  return <BuildingView key={building.id} {...shared} property={fp.property} building={building} floorId={floorId} />
}

// ── property (site) level ───────────────────────────────────────────────────
function PropertyView({ fp, property, stateOf, nameOf, planStyle, cyclePlan }) {
  const { isMobile, sp, accent } = useTheme()
  const [edit, setEdit] = useState(false)
  const [tool, setTool] = useState('select')
  const [selected, setSelected] = useState(null)       // {type: 'room', id: buildingId} (buildings are the site's rooms)
  const [listView, setListView] = useState(() => lsGet('senseless.map.plist') === '1')
  const [zoom, setZoom] = useState(null)               // building being zoomed into (transition)
  useEffect(() => { lsSet('senseless.map.plist', listView ? '1' : '0') }, [listView])
  useEffect(() => { if (!edit) { setTool('select'); setSelected(null) } }, [edit])
  useEffect(() => {
    const onKey = e => {
      if (busyTyping()) return
      if (e.key === 'Escape') { if (tool === 'room') setTool('select'); else setSelected(null) }
      if (e.key === 'e' && !e.metaKey && !e.ctrlKey && !e.altKey) setEdit(v => !v)
      if (e.key === 'r' && edit && !e.metaKey && !e.ctrlKey && !e.altKey) setTool(t => (t === 'room' ? 'select' : 'room'))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tool, edit])

  const { floor: site, rollup } = siteAsFloor(property, stateOf)
  const unplaced = property.buildings.filter(b => b.x == null)
  const totals = property.buildings.map(b => ({ b, r: buildingRollup(b, stateOf) }))
  const watts = totals.reduce((a, t) => a + t.r.watts, 0)
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

  const open = b => {
    if (reduced || b.x == null) { navigate(mapPath(b.id)); return }
    setZoom(b)                                          // zoom toward the footprint, then switch views
    setTimeout(() => navigate(mapPath(b.id)), 260)
  }
  const onSelect = sel => {
    if (!sel) { setSelected(null); return }
    if (!edit && sel.type === 'room') { const b = property.buildings.find(x => x.id === sel.id); if (b) open(b); return }
    setSelected(sel)
  }
  const onDrawRoom = async r => {
    const b = await fp.addBuilding({ name: `Building ${property.buildings.length + 1}`, icon: 'other', ...r })
    setTool('select')
    setSelected({ type: 'room', id: b.id })
  }
  const uploadSite = async file => {
    if (!IMAGE_TYPES.includes(file.type)) { toast('Use a PNG, JPEG or WebP image'); return }
    toast('Uploading…')
    await fp.uploadSiteImage(file).then(() => toast('Site plan saved')).catch(() => {})
  }
  const addBuilding = async name => {
    const b = await fp.addBuilding({ name, icon: guessBuildingIcon(name), ...(property.image ? defaultFootprint(property) : {}) })
    setSelected({ type: 'room', id: b.id })
  }

  const selBuilding = edit && selected?.type === 'room' ? property.buildings.find(b => b.id === selected.id) : null
  const height = isMobile ? 'calc(100svh - 300px)' : 'calc(100vh - 260px)'
  const panel = selBuilding ? (
    <BuildingInspector b={selBuilding} rollup={buildingRollup(selBuilding, stateOf)} placed={selBuilding.x != null}
      onPatch={body => fp.patchBuilding(selBuilding.id, body).catch(() => {})}
      onPlace={() => fp.patchBuilding(selBuilding.id, defaultFootprint(property)).catch(() => {})}
      onUnplace={() => fp.patchBuilding(selBuilding.id, { x: null, y: null, w: null, h: null }).catch(() => {})}
      onOpen={() => navigate(mapPath(selBuilding.id))}
      onDelete={() => { fp.deleteBuilding(selBuilding.id).catch(() => {}); setSelected(null) }}
      onClose={() => setSelected(null)} />
  ) : edit ? (
    <SiteSettings property={property} onRename={name => fp.patchProperty({ name }).catch(() => {})} onAdd={addBuilding}
      onReplaceImage={uploadSite} onRemoveImage={() => fp.removeSiteImage().catch(() => {})} />
  ) : null

  const canvas = (
    <div style={{ transformOrigin: zoom ? `${(zoom.x + zoom.w / 2) * 100}% ${(zoom.y + zoom.h / 2) * 100}%` : '50% 50%',
      transform: zoom ? 'scale(2.6)' : 'none', opacity: zoom ? 0 : 1, transition: 'transform .26s ease-in, opacity .26s ease-in' }}>
      <MapCanvas floor={site} rollup={rollup} stateOf={stateOf} nameOf={nameOf} edit={edit} tool={tool} placing={null}
        selected={selected} onSelect={onSelect} onPlace={() => {}} onMoveMarker={() => {}} onDeleteMarker={() => {}}
        onDrawRoom={onDrawRoom} onPatchRoom={(id, b) => fp.patchBuilding(id, b).catch(() => {})} onDropFile={edit ? uploadSite : null}
        height={height} planStyle={planStyle} onCyclePlan={cyclePlan}>
        {edit && <EditBar tool={tool} setTool={setTool} placing={null} onCancelPlace={() => {}} roomWord="building" />}
        {!property.image && !rollup.rooms.length && (
          <UploadPrompt edit={edit} onFile={uploadSite} onEdit={() => setEdit(true)} title="Add a site plan"
            text="An aerial screenshot of your lot, a survey, or a sketch. Then draw each building where it sits. PNG, JPEG or WebP, up to 15 MB." />
        )}
      </MapCanvas>
    </div>
  )

  return (
    <div>
      <style>{BREATHE_CSS}</style>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: sp(12), flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: C.bright }}>{property.name}</h1>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{property.buildings.length} building{property.buildings.length === 1 ? '' : 's'}</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}><span style={{ color: C.bright }}>{fmtWatts(watts)}</span> mapped</span>
        <Segmented value={listView ? 'list' : 'map'} onChange={v => setListView(v === 'list')} options={[['map', 'Map'], ['list', 'List']]} label="View" />
        <Button variant={edit ? 'primary' : 'default'} onClick={() => setEdit(v => !v)} aria-pressed={edit} title="Edit (E)">{edit ? 'Done' : 'Edit'}</Button>
      </div>

      {listView ? (
        <Outline property={property} stateOf={stateOf} nameOf={nameOf} />
      ) : (
        <>
          <BuildingTiles items={totals.filter(t => t.b.x == null || !property.image)} accent={accent} onOpen={open}
            edit={edit} canPlace={!!property.image} onPlace={b => fp.patchBuilding(b.id, defaultFootprint(property)).catch(() => {})}
            onSelect={b => setSelected({ type: 'room', id: b.id })} selectedId={selBuilding?.id} />
          {(property.image || rollup.rooms.length > 0 || edit || !unplaced.length) && (isMobile
            ? <>{canvas}{panel && <BottomSheet onClose={() => setSelected(null)}>{panel}</BottomSheet>}</>
            : <div style={{ display: 'grid', gridTemplateColumns: panel ? 'minmax(0, 1fr) 320px' : 'minmax(0, 1fr)', gap: sp(14), alignItems: 'start' }}>
                {canvas}
                {panel && <Panel style={{ padding: 16, maxHeight: height, overflowY: 'auto' }}>{panel}</Panel>}
              </div>)}
          {!edit && !property.image && !rollup.rooms.length && unplaced.length > 0 && (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 10 }}>
              Add a site plan in Edit to lay the buildings out where they really are.
            </div>
          )}
        </>
      )}
    </div>
  )
}

// Buildings not on the site plan (or every building, when there's no plan yet), as live tiles.
function BuildingTiles({ items, accent, onOpen, edit, canPlace, onPlace, onSelect, selectedId }) {
  if (!items.length) return null
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))', gap: 10, marginBottom: 14 }}>
      {items.map(({ b, r }) => {
        const hot = r.watts >= 1000, lit = r.watts > 0
        return (
          <div key={b.id} style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: 8, padding: 14, borderRadius: 14, background: C.panel,
            border: `1px solid ${selectedId === b.id ? accent : hot ? alpha(C.orange, '80') : lit ? alpha(accent, '55') : C.line}`,
            boxShadow: hot ? `0 0 22px ${alpha(C.orange, '30')}` : lit ? `0 0 16px ${alpha(accent, '1c')}` : 'none' }}>
            <button onClick={() => (edit ? onSelect(b) : onOpen(b))} aria-label={`${edit ? 'Edit' : 'Open'} ${b.name}`}
              style={{ all: 'unset', cursor: 'pointer', display: 'flex', gap: 10, alignItems: 'center' }}>
              <Icon name={b.icon} size={26} color={hot ? C.orange : lit ? accent : C.muted} />
              <span style={{ minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 15, fontWeight: 600, color: C.bright, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.name}</span>
                <span style={{ display: 'block', fontFamily: MONO, fontSize: 11, color: C.muted }}>
                  {r.floors} floor{r.floors === 1 ? '' : 's'} · {r.count} device{r.count === 1 ? '' : 's'}
                </span>
              </span>
            </button>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontFamily: MONO, fontVariantNumeric: 'tabular-nums' }}>
              <span style={{ fontSize: 20, fontWeight: 700, color: lit ? C.bright : C.dim }}>{lit ? fmtWatts(r.watts) : 'idle'}</span>
              {r.wh > 0 && <span style={{ fontSize: 11, color: C.dim }}>{fmtWh(r.wh)} today</span>}
              {edit && canPlace && <Button size="sm" variant="ghost" style={{ marginLeft: 'auto' }} onClick={() => onPlace(b)}>Place on plan</Button>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function BuildingInspector({ b, rollup, placed, onPatch, onPlace, onUnplace, onOpen, onDelete, onClose }) {
  const { accent } = useTheme()
  const [name, setName] = useState(b.name)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => { setName(b.name); setConfirm(false) }, [b.id, b.name])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', color: accent }}>Building</span>
        <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 'none', color: C.muted, cursor: 'pointer', fontSize: 18 }}>×</button>
      </div>
      <TextInput value={name} maxLength={60} aria-label="Building name" onChange={e => setName(e.target.value)}
        onBlur={() => { const n = name.trim(); if (n && n !== b.name) onPatch({ name: n }); else setName(b.name) }}
        onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} />
      <div role="radiogroup" aria-label="Building icon" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {BUILDING_ICONS.map(([id, label]) => (
          <button key={id} role="radio" aria-checked={b.icon === id} title={label} aria-label={label} onClick={() => onPatch({ icon: id })}
            style={{ width: 40, height: 40, display: 'grid', placeItems: 'center', borderRadius: 9, cursor: 'pointer',
              background: b.icon === id ? alpha(accent, '1c') : 'transparent', border: `1px solid ${b.icon === id ? alpha(accent, '80') : C.line2}` }}>
            <Icon name={id} size={20} color={b.icon === id ? accent : C.muted} />
          </button>
        ))}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.7 }}>
        {rollup.floors} floor{rollup.floors === 1 ? '' : 's'} · {rollup.count} device{rollup.count === 1 ? '' : 's'} · {rollup.watts > 0 ? fmtWatts(rollup.watts) : 'idle'}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button variant="primary" onClick={onOpen}>Open floors</Button>
        {placed ? <Button variant="ghost" onClick={onUnplace}>Take off plan</Button> : <Button variant="ghost" onClick={onPlace}>Place on plan</Button>}
      </div>
      <div style={{ height: 1, background: C.line }} />
      {confirm
        ? <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span style={{ fontSize: 13, color: C.body }}>Delete {b.name} with its {rollup.floors} floor{rollup.floors === 1 ? '' : 's'}, rooms and {rollup.count} device marker{rollup.count === 1 ? '' : 's'}? Load names are kept.</span>
            <div style={{ display: 'flex', gap: 8 }}><Button variant="danger" onClick={onDelete}>Delete</Button><Button variant="ghost" onClick={() => setConfirm(false)}>Keep</Button></div>
          </div>
        : <Button variant="ghost" onClick={() => setConfirm(true)} style={{ alignSelf: 'flex-start', color: C.muted }}>Delete building…</Button>}
    </div>
  )
}

function SiteSettings({ property, onRename, onAdd, onReplaceImage, onRemoveImage }) {
  const { accent } = useTheme()
  const [name, setName] = useState(property.name)
  const [newName, setNewName] = useState('')
  const fileRef = useRef(null)
  useEffect(() => setName(property.name), [property.name])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <span style={{ fontFamily: MONO, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', color: accent }}>Property</span>
      <TextInput value={name} maxLength={60} aria-label="Property name" onChange={e => setName(e.target.value)}
        onBlur={() => { const n = name.trim(); if (n && n !== property.name) onRename(n); else setName(property.name) }}
        onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} />
      <form onSubmit={e => { e.preventDefault(); const n = newName.trim(); if (n) { onAdd(n); setNewName('') } }} style={{ display: 'flex', gap: 6 }}>
        <TextInput value={newName} onChange={e => setNewName(e.target.value)} placeholder="Detached garage" maxLength={60} aria-label="New building name" style={{ flex: 1, minWidth: 0 }} />
        <Button type="submit" variant="primary">Add building</Button>
      </form>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.7 }}>
        Or pick <b>Building</b> in the toolbar (R) and drag on the plan to draw one where it sits.
      </div>
      <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) onReplaceImage(f) }} />
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button onClick={() => fileRef.current.click()}>{property.image ? 'Replace site plan' : 'Upload site plan'}</Button>
        {property.image && <Button variant="ghost" onClick={onRemoveImage}>Remove plan</Button>}
      </div>
    </div>
  )
}

// Every building → floor → room → device, with live totals. The management view.
function Outline({ property, stateOf, nameOf }) {
  const { accent } = useTheme()
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {property.buildings.map(b => {
        const r = buildingRollup(b, stateOf)
        return (
          <section key={b.id} aria-label={b.name}>
            <a href={mapPath(b.id)} onClick={link(mapPath(b.id))} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, color: 'inherit', textDecoration: 'none' }}>
              <Icon name={b.icon} size={22} color={r.watts > 0 ? accent : C.muted} />
              <span style={{ fontSize: 18, fontWeight: 700, color: C.bright }}>{b.name}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: r.watts > 0 ? accent : C.dim }}>{r.watts > 0 ? fmtWatts(r.watts) : 'idle'} · {r.on} on · {r.floors} floor{r.floors === 1 ? '' : 's'}</span>
            </a>
            {b.floors.length
              ? <ListView floors={b.floors} stateOf={stateOf} nameOf={nameOf} onPick={(f, sel) => navigate(`${mapPath(b.id, f.id)}${sel ? `?sel=${sel.type}:${sel.id}` : ''}`)} />
              : <Panel style={{ padding: 14, fontFamily: MONO, fontSize: 11, color: C.dim }}>No floors yet. Open it to add one.</Panel>}
          </section>
        )
      })}
    </div>
  )
}

// ── building level: its floors, rooms and devices ───────────────────────────
function BuildingView({ fp, property, building, floorId, stateOf, nameOf, loadsByName, senseDevices, planStyle, cyclePlan }) {
  const { isMobile, sp } = useTheme()
  const { query } = useLocation()
  const [edit, setEdit] = useState(false)
  const [tool, setTool] = useState('select')         // select | room
  const [placing, setPlacing] = useState(null)       // palette item armed for tap-to-place
  const [selected, setSelected] = useState(null)     // {type: 'marker'|'room', id}
  const canvasApi = useRef(null)
  const [listView, setListView] = useState(() => lsGet('senseless.map.list') === '1')

  const floors = building.floors
  const floor = floors.find(f => f.id === floorId) ?? defaultFloor(building)
  const pickFloor = id => navigate(mapPath(building.id, id), { replace: true })
  useEffect(() => { lsSet('senseless.map.list', listView ? '1' : '0') }, [listView])
  // ?sel=marker:12 (from the property outline) selects once the floor is showing
  useEffect(() => {
    const m = /^(marker|room):(\d+)$/.exec(query.get('sel') || '')
    if (m && floor) { setSelected({ type: m[1], id: Number(m[2]) }); navigate(mapPath(building.id, floor.id), { replace: true }) }
  }, [query, floor?.id])

  const loadNames = useMemo(() => [...new Set([...loadsByName.values()].map(g => g.name))].sort((a, b) => a.localeCompare(b)), [loadsByName])
  const placedRefs = useMemo(() => new Set((fp.floors ?? []).flatMap(f => f.markers.map(m => `${m.kind}:${m.ref}`))), [fp.floors])

  // Selection and edit state reset on floor / mode changes; Escape backs out one step.
  useEffect(() => { setSelected(s => (query.get('sel') ? s : null)); setPlacing(null) }, [floor?.id])
  useEffect(() => { if (!edit) { setTool('select'); setPlacing(null) } }, [edit])
  useEffect(() => {
    const onKey = e => {
      if (busyTyping()) return
      if (e.key === 'Escape') { if (placing) setPlacing(null); else if (tool === 'room') setTool('select'); else if (selected) setSelected(null); else if (!edit) navigate('/map') }
      if (e.key === 'e' && !e.metaKey && !e.ctrlKey && !e.altKey && floor) setEdit(v => !v)
      if (e.key === 'r' && edit && !e.metaKey && !e.ctrlKey && !e.altKey) setTool(t => (t === 'room' ? 'select' : 'room'))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [placing, tool, edit, floor, selected])

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
  const addFloor = async (name, where = 'above') => {
    const levels = floors.map(f => f.level)
    const level = !levels.length ? 0 : where === 'below' ? Math.min(...levels) - 1 : Math.max(...levels) + 1
    const f = await fp.addFloor(name, building.id, level)
    pickFloor(f.id)
    setEdit(true)
  }

  const header = (
    <nav aria-label="Breadcrumb" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: sp(10), flexWrap: 'wrap', fontFamily: MONO, fontSize: 12 }}>
      <a href="/map" onClick={link('/map')} style={{ color: C.muted, textDecoration: 'none' }}>← {property.name}</a>
      <span style={{ color: C.dim }}>/</span>
      <Icon name={building.icon} size={18} color={C.body} />
      <select value={building.id} onChange={e => navigate(mapPath(Number(e.target.value)))} aria-label="Building"
        style={{ background: 'transparent', color: C.bright, border: 'none', fontFamily: 'inherit', fontSize: 15, fontWeight: 700, cursor: 'pointer', padding: 0 }}>
        {property.buildings.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
    </nav>
  )

  if (!floor) {
    return (
      <div>
        {header}
        <Onboarding title={`Add ${building.name}'s first floor`} text="Name it, then upload its floor plan (or use a blank grid) and place devices."
          initial={building.icon === 'house' ? 'Main floor' : 'Ground floor'} label="Floor name" cta="Create floor" onCreate={name => addFloor(name)} />
      </div>
    )
  }

  const roll = rollups(floor, stateOf)
  const selMarker = selected?.type === 'marker' && floor.markers.find(m => m.id === selected.id)
  const selRoom = selected?.type === 'room' && roll.rooms.find(r => r.id === selected.id)
  const height = isMobile ? 'calc(100svh - 270px)' : 'calc(100vh - 245px)'

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
          onDelete={() => { fp.deleteFloor(floor.id).then(() => { navigate(mapPath(building.id), { replace: true }); setEdit(false) }).catch(() => {}) }} />
      </>
    )
  }

  const canvas = (
    <div style={{ animation: 'sl-zoomin .3s ease-out' }}>
      <MapCanvas floor={floor} rollup={roll} stateOf={stateOf} nameOf={nameOf} edit={edit} tool={tool} placing={placing}
        selected={selected} onSelect={setSelected} onPlace={onPlace} onMoveMarker={onMoveMarker} onDeleteMarker={onDeleteMarker}
        onDrawRoom={onDrawRoom} onPatchRoom={onPatchRoom} onDropFile={edit ? upload : null} height={height} planStyle={planStyle} onCyclePlan={cyclePlan} apiRef={canvasApi}>
        {edit && <EditBar tool={tool} setTool={setTool} placing={placing} onCancelPlace={() => setPlacing(null)} />}
        {!floor.image_url && !floor.markers.length && !floor.rooms.length && <UploadPrompt edit={edit} onFile={upload} onEdit={() => setEdit(true)} />}
        {!edit && floor.markers.length > 0 && <Legend />}
      </MapCanvas>
    </div>
  )

  return (
    <div>
      <style>{BREATHE_CSS}</style>
      {header}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: sp(12), flexWrap: 'wrap' }}>
        <FloorSwitcher floors={floors} current={floor.id} onPick={pickFloor} watts={f => rollups(f, stateOf).watts} edit={edit} onAdd={addFloor} />
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
        ? <ListView floors={floors} stateOf={stateOf} nameOf={nameOf} onPick={(f, sel) => { pickFloor(f.id); setListView(false); setTimeout(() => setSelected(sel), 0) }} />
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

const BREATHE_CSS = `
  @keyframes sl-breathe { 0%, 100% { transform: scale(1) } 50% { transform: scale(1.07) } }
  .sl-breathe { animation: sl-breathe 3.2s ease-in-out infinite }
  @keyframes sl-zoomin { from { transform: scale(.94); opacity: 0 } to { transform: none; opacity: 1 } }
  @media (prefers-reduced-motion: reduce) { .sl-breathe { animation: none } [style*="sl-zoomin"] { animation: none !important } }
`

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
  const [adding, setAdding] = useState(null)           // 'above' | 'below' while naming a new floor
  const [name, setName] = useState('')
  return (
    <div role="tablist" aria-label="Floors" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {floors.map(f => {
        const on = f.id === current, w = watts(f)
        return (
          <button key={f.id} role="tab" aria-selected={on} onClick={() => onPick(f.id)} title={`Level ${f.level}`}
            style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '7px 12px', borderRadius: 9, cursor: 'pointer',
              border: `1px solid ${on ? alpha(accent, '80') : C.line}`, background: on ? alpha(accent, '14') : C.panel }}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: on ? accent : C.dim }}>{levelTag(f.level)}</span>
            <span style={{ fontSize: 14, fontWeight: 600, color: on ? C.bright : C.body }}>{f.name}</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: w > 0 ? (on ? accent : C.muted) : C.dim, fontVariantNumeric: 'tabular-nums' }}>{w > 0 ? fmtWatts(w) : '—'}</span>
          </button>
        )
      })}
      {edit && (adding
        ? <form onSubmit={e => { e.preventDefault(); if (name.trim()) { onAdd(name.trim(), adding); setName(''); setAdding(null) } }} style={{ display: 'flex', gap: 6 }}>
            <TextInput autoFocus value={name} onChange={e => setName(e.target.value)} placeholder={adding === 'below' ? 'Basement' : 'Upstairs'} maxLength={60} style={{ width: 140 }}
              onBlur={() => !name && setAdding(null)} aria-label={`New floor ${adding}`} />
            <Button type="submit" variant="primary" size="sm">Add</Button>
          </form>
        : <>
            <Button size="sm" variant="ghost" onClick={() => setAdding('above')}>+ Floor above</Button>
            <Button size="sm" variant="ghost" onClick={() => setAdding('below')}>+ Below</Button>
          </>)}
    </div>
  )
}

function EditBar({ tool, setTool, placing, onCancelPlace, roomWord = 'room' }) {
  const { accent } = useTheme()
  const hint = placing ? `Tap the map to place ${placing.title.split(' (')[0]}` : tool === 'room' ? `Drag on the map to draw a ${roomWord}` : roomWord === 'room' ? 'Drag devices to move them · tap to edit' : 'Drag buildings to move them · tap to edit'
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ position: 'absolute', left: 10, top: 10, right: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', pointerEvents: 'none' }}>
      <div style={{ display: 'inline-flex', gap: 2, padding: 3, borderRadius: 9, background: alpha(C.panel, 'ee'), border: `1px solid ${C.line2}`, pointerEvents: 'auto' }}>
        {[['select', 'Select', 'Select and move (Esc)'], ['room', roomWord === 'room' ? 'Room' : 'Building', `Draw a ${roomWord} (R)`]].map(([v, t, title]) => (
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

function UploadPrompt({ edit, onFile, onEdit, title = 'Add a floor plan', text = 'Drop a blueprint, a phone photo of a sketch, or a screenshot from any floor-plan app. PNG, JPEG or WebP, up to 15 MB.' }) {
  const { accent } = useTheme()
  const ref = useRef(null)
  const [dismissed, setDismissed] = useState(false)
  if (dismissed) return null
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: 16, background: alpha(C.bg, '99') }}>
      <div style={{ maxWidth: 420, textAlign: 'center', padding: '28px 24px', borderRadius: 14, background: C.panel, border: `1px dashed ${alpha(accent, '70')}` }}>
        <Icon name="panel" size={34} color={accent} style={{ margin: '0 auto 12px' }} />
        <div style={{ fontSize: 18, fontWeight: 700, color: C.bright, marginBottom: 6 }}>{title}</div>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.7, marginBottom: 16 }}>
          {text}
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
        if (!groups.length && !rest.length) {
          return (
            <button key={f.id} onClick={() => onPick(f, null)} style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '10px 16px', borderRadius: 12, cursor: 'pointer',
              background: C.panel, border: `1px solid ${C.line}`, textAlign: 'left' }}>
              <span style={{ fontSize: 15, fontWeight: 600, color: C.body }}>{f.name}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>nothing placed yet</span>
            </button>
          )
        }
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

function Onboarding({ title, text, initial, label, cta, onCreate }) {
  const { accent } = useTheme()
  const [name, setName] = useState(initial)
  return (
    <div style={{ maxWidth: 560, margin: '40px auto', textAlign: 'center' }}>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 10, marginBottom: 18, color: accent }}>
        {['house', 'garage', 'greenhouse', 'pool', 'shed'].map((i, k) => <Icon key={i} name={i} size={26} color={k === 0 ? accent : k === 2 ? C.orange : C.dim} />)}
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color: C.bright, marginBottom: 10 }}>{title}</div>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.muted, lineHeight: 1.75, marginBottom: 22 }}>{text}</div>
      <form onSubmit={e => { e.preventDefault(); if (name.trim()) onCreate(name.trim()) }} style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
        <TextInput value={name} onChange={e => setName(e.target.value)} maxLength={60} aria-label={label} style={{ width: 200 }} />
        <Button type="submit" variant="primary">{cta}</Button>
      </form>
    </div>
  )
}
