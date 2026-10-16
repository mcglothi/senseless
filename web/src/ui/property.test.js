import { describe, expect, it } from 'vitest'
import { buildingRollup, defaultFloor, defaultFootprint, guessBuildingIcon, levelTag, mapPath, parseMapPath, siteAsFloor } from './property'

const stateOf = m => ({ on: m.w > 0, w: m.w, whToday: m.wh ?? 0 })

describe('property helpers', () => {
  it('parses and builds map paths', () => {
    expect(parseMapPath('/map')).toEqual({ buildingId: null, floorId: null })
    expect(parseMapPath('/map/b/3')).toEqual({ buildingId: 3, floorId: null })
    expect(parseMapPath('/map/b/3/f/7')).toEqual({ buildingId: 3, floorId: 7 })
    expect(parseMapPath('/map/b/x')).toEqual({ buildingId: null, floorId: null })
    expect(mapPath(null)).toBe('/map')
    expect(mapPath(3)).toBe('/map/b/3')
    expect(mapPath(3, 7)).toBe('/map/b/3/f/7')
  })
  it('opens a building on its ground floor', () => {
    expect(defaultFloor({ floors: [{ id: 1, level: 2 }, { id: 2, level: 0 }, { id: 3, level: -1 }] }).id).toBe(2)
    expect(defaultFloor({ floors: [{ id: 1, level: 2 }, { id: 3, level: 1 }] }).id).toBe(3)
    expect(defaultFloor({ floors: [{ id: 3, level: -1 }] }).id).toBe(3)
    expect(defaultFloor({ floors: [] })).toBe(null)
    expect([levelTag(0), levelTag(-1), levelTag(2)]).toEqual(['G', 'B1', '2'])
  })
  it('rolls up a building across floors', () => {
    const b = { floors: [{ rooms: [], markers: [{ w: 100, wh: 50 }, { w: 0 }] }, { rooms: [], markers: [{ w: 1200, wh: 300 }] }] }
    expect(buildingRollup(b, stateOf)).toEqual({ watts: 1300, wh: 350, on: 2, count: 3, floors: 2 })
  })
  it('turns placed buildings into the site canvas rooms', () => {
    const p = { name: 'Home', image: '/img', img_w: 10, img_h: 5, buildings: [
      { id: 1, name: 'House', icon: 'house', x: 0.1, y: 0.1, w: 0.3, h: 0.3, floors: [{ rooms: [], markers: [{ w: 400 }] }] },
      { id: 2, name: 'Shed', icon: 'shed', x: null, y: null, w: null, h: null, floors: [] }] }
    const { floor, rollup } = siteAsFloor(p, stateOf)
    expect(floor.image_url).toBe('/img')
    expect(floor.rooms.map(r => r.id)).toEqual([1])
    expect(rollup.watts).toBe(400)
  })
  it('finds a free spot for a new building', () => {
    const a = defaultFootprint({ buildings: [] })
    const b = defaultFootprint({ buildings: [{ ...a }] })
    expect(a).not.toEqual(b)
    expect(b.x + b.w).toBeLessThanOrEqual(1)
  })
  it('guesses building icons', () => {
    expect(['Detached garage', 'Greenhouse', 'Pool house', 'Garden shed', 'Main house', 'Thing'].map(guessBuildingIcon))
      .toEqual(['garage', 'greenhouse', 'pool', 'shed', 'house', 'other'])
  })
})
