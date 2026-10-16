// Property map helpers (buildings → floors), pure so they're unit-tested (property.test.js).
import { rollups } from './floorplan'

// /map, /map/b/3, /map/b/3/f/7 → { buildingId, floorId } (null when absent or malformed).
export function parseMapPath(path) {
  const m = /^\/map(?:\/b\/(\d+)(?:\/f\/(\d+))?)?\/?$/.exec(path || '')
  if (!m) return { buildingId: null, floorId: null }
  return { buildingId: m[1] ? Number(m[1]) : null, floorId: m[2] ? Number(m[2]) : null }
}

export const mapPath = (buildingId, floorId) =>
  buildingId == null ? '/map' : floorId == null ? `/map/b/${buildingId}` : `/map/b/${buildingId}/f/${floorId}`

// The floor a building opens on: ground (level 0), else the lowest floor above ground, else the highest.
export function defaultFloor(building) {
  const fs = building?.floors ?? []
  if (!fs.length) return null
  return fs.find(f => f.level === 0) ?? [...fs].filter(f => f.level > 0).sort((a, b) => a.level - b.level)[0] ?? fs[0]
}

// "Basement", "Ground", "Level 2" style tags for the floor stack.
export function levelTag(level) {
  if (level === 0) return 'G'
  return level < 0 ? `B${-level}` : `${level}`
}

// Live totals for a building, from its floors' markers.
export function buildingRollup(building, stateOf) {
  let watts = 0, wh = 0, on = 0, count = 0
  for (const f of building.floors ?? []) {
    const r = rollups(f, stateOf)
    watts += r.watts
    on += r.on
    for (const m of f.markers ?? []) { count++; wh += stateOf(m).whToday ?? 0 }
  }
  return { watts, wh, on, count, floors: (building.floors ?? []).length }
}

// The site plan as a floor-like object for MapCanvas: placed buildings become its "rooms", so the
// canvas's room drawing, moving and resizing edit building footprints.
export function siteAsFloor(property, stateOf) {
  const placed = (property?.buildings ?? []).filter(b => b.x != null)
  const rooms = placed.map(b => {
    const r = buildingRollup(b, stateOf)
    return { id: b.id, name: b.name, x: b.x, y: b.y, w: b.w, h: b.h, icon: b.icon, watts: r.watts, wh: r.wh, count: r.count, on: r.on }
  })
  return {
    floor: { id: 'site', name: property?.name ?? 'Property', image_url: property?.image ?? null, img_w: property?.img_w, img_h: property?.img_h, rooms, markers: [] },
    rollup: { rooms, watts: rooms.reduce((a, r) => a + r.watts, 0), on: rooms.reduce((a, r) => a + r.on, 0) },
  }
}

// Where to drop a building placed without drawing: a free-ish slot in a 3x3 grid of the site.
export function defaultFootprint(property) {
  const taken = (property?.buildings ?? []).filter(b => b.x != null)
  for (let i = 0; i < 9; i++) {
    const x = 0.06 + (i % 3) * 0.31, y = 0.08 + Math.floor(i / 3) * 0.3
    if (!taken.some(b => Math.abs(b.x - x) < 0.15 && Math.abs(b.y - y) < 0.15)) return { x, y, w: 0.24, h: 0.2 }
  }
  return { x: 0.38, y: 0.4, w: 0.24, h: 0.2 }
}

export const BUILDING_ICONS = [
  ['house', 'House'], ['garage', 'Garage'], ['barn', 'Barn'], ['shed', 'Shed'], ['greenhouse', 'Greenhouse'],
  ['pool', 'Pool house'], ['workshop', 'Workshop'], ['office', 'Office'], ['other', 'Other'],
]

export function guessBuildingIcon(name = '') {
  const n = name.toLowerCase()
  if (/garage|carport/.test(n)) return 'garage'
  if (/green ?house|grow/.test(n)) return 'greenhouse'
  if (/pool|spa|hot ?tub|cabana/.test(n)) return 'pool'
  if (/shed/.test(n)) return 'shed'
  if (/barn|stable|coop/.test(n)) return 'barn'
  if (/shop|studio/.test(n)) return 'workshop'
  if (/office/.test(n)) return 'office'
  if (/house|home|cottage|cabin|adu|in-?law/.test(n)) return 'house'
  return 'other'
}
