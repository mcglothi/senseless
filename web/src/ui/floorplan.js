// Pure floor-plan logic (tested in floorplan.test.js): live state per marker, room/floor rollups,
// coordinate conversion and the visual tier of a device.

// Live state of one marker given detected loads and Sense's live device list.
//   load  -> the named load (all patterns sharing the name): running, ≈ watts (pattern size), today's Wh
//   sense -> Sense's live per-device watts (absent from the feed = off)
export function markerState(m, { loadsByName, senseDevices }) {
  if (m.kind === 'load') {
    const g = loadsByName.get((m.ref ?? '').trim().toLowerCase())
    if (!g) return { known: false, on: false, w: 0, whToday: null }
    return { known: true, on: !!g.running, w: g.running ? Math.round(g.center_w) : 0, whToday: g.wh_today ?? null, runsToday: g.runs_today, estimated: true }
  }
  if (m.kind === 'sense') {
    const d = senseDevices.find(x => String(x.id) === String(m.ref))
    const w = d?.w ?? 0
    return { known: true, on: w >= 5, w: Math.round(w), whToday: null, estimated: false }
  }
  return { known: true, on: false, w: 0, whToday: null, label: true }
}

// Group named patterns by name (case-insensitive), combining stats, for marker lookups.
export function loadsIndex(patterns) {
  const idx = new Map()
  for (const p of patterns) {
    if (!p.name) continue
    const k = p.name.trim().toLowerCase()
    const g = idx.get(k)
    if (!g) idx.set(k, { name: p.name, running: !!p.running, center_w: p.running ? p.center_w : 0, wh_today: p.wh_today ?? 0, runs_today: p.runs_today ?? 0, legs: p.legs })
    else {
      g.running = g.running || !!p.running
      if (p.running) g.center_w += p.center_w
      g.wh_today += p.wh_today ?? 0
      g.runs_today += p.runs_today ?? 0
    }
  }
  // a group that's off still needs a size for "≈ W when on" in the inspector
  for (const p of patterns) {
    const g = p.name && idx.get(p.name.trim().toLowerCase())
    if (g && !g.running) g.typical_w = Math.max(g.typical_w ?? 0, p.center_w)
  }
  return idx
}

// Visual tier: 0 off, 1 standby (<15 W), 2 active, 3 heavy (>= 1 kW). Heavy is the only one that animates.
export function tier(state) {
  if (!state.on || state.w < 1) return 0
  if (state.w < 15) return 1
  if (state.w < 1000) return 2
  return 3
}

export const inRoom = (m, r) => m.x >= r.x && m.x <= r.x + r.w && m.y >= r.y && m.y <= r.y + r.h

// Live watts and today's energy per room (devices inside it) and per floor (all its devices).
// `watts`, never `w`: a room's `w` is its width.
export function rollups(floor, stateOf) {
  const rooms = (floor.rooms ?? []).map(r => {
    const inside = (floor.markers ?? []).filter(m => inRoom(m, r))
    const states = inside.map(stateOf)
    return { ...r, watts: states.reduce((a, s) => a + s.w, 0), wh: states.reduce((a, s) => a + (s.whToday ?? 0), 0), count: inside.length, on: states.filter(s => s.on).length }
  })
  const all = (floor.markers ?? []).map(stateOf)
  return { rooms, watts: all.reduce((a, s) => a + s.w, 0), on: all.filter(s => s.on).length }
}

// Client point -> normalized image coordinates, given the rendered image's bounding rect (transform included).
export function toNormalized(clientX, clientY, rect) {
  const x = (clientX - rect.left) / rect.width, y = (clientY - rect.top) / rect.height
  return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)), inside: x >= 0 && x <= 1 && y >= 0 && y <= 1 }
}

// Normalized rectangle from two corner points (any drag direction), clamped to the image.
export function rectFrom(a, b) {
  const x = Math.max(0, Math.min(a.x, b.x)), y = Math.max(0, Math.min(a.y, b.y))
  const x2 = Math.min(1, Math.max(a.x, b.x)), y2 = Math.min(1, Math.max(a.y, b.y))
  return { x, y, w: x2 - x, h: y2 - y }
}

// Zoom around a point: keep the content under (px, py) fixed while scale changes.
export function zoomAt(view, factor, px, py, min = 0.3, max = 8) {
  const scale = Math.min(max, Math.max(min, view.scale * factor))
  const k = scale / view.scale
  return { scale, x: px - (px - view.x) * k, y: py - (py - view.y) * k }
}

// Fit an image of (iw, ih) into a box (bw, bh) with a margin, centred.
export function fitView(iw, ih, bw, bh, margin = 24) {
  if (!iw || !ih || !bw || !bh) return { scale: 1, x: 0, y: 0 }
  const scale = Math.min((bw - 2 * margin) / iw, (bh - 2 * margin) / ih)
  return { scale, x: (bw - iw * scale) / 2, y: (bh - ih * scale) / 2 }
}

export const ICONS = ['plug', 'bolt', 'fan', 'heater', 'ac', 'fridge', 'microwave', 'oven', 'washer', 'dryer', 'dishwasher',
  'pump', 'water', 'light', 'tv', 'computer', 'router', 'car', 'tub', 'tool', 'panel', 'other']

// A sensible default icon from a load or device name.
export function guessIcon(name = '') {
  const n = name.toLowerCase()
  const rules = [[/micro/, 'microwave'], [/fridge|freezer|refriger/, 'fridge'], [/heat pump|\ba\/?c\b|air cond/, 'ac'], [/heat|furnace/, 'heater'],
    [/fan|blower/, 'fan'], [/pump|well|sump/, 'pump'], [/water|dispenser|kettle/, 'water'], [/dryer/, 'dryer'], [/wash/, 'washer'],
    [/dish/, 'dishwasher'], [/oven|range|stove|toast/, 'oven'], [/light|lamp/, 'light'], [/\btv\b|tele/, 'tv'], [/computer|\bpc\b|server/, 'computer'],
    [/router|network|modem/, 'router'], [/\bev\b|\bcars?\b|charger/, 'car'], [/tub|jacuzzi|spa|hot tub/, 'tub'], [/garage|tool|saw/, 'tool'], [/panel|breaker/, 'panel']]
  return rules.find(([re]) => re.test(n))?.[1] ?? 'plug'
}
