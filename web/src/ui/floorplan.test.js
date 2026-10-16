import { describe, expect, it } from 'vitest'
import { fitView, guessIcon, loadsIndex, markerState, rectFrom, rollups, tier, toNormalized, zoomAt } from './floorplan'

const patterns = [
  { id: 1, name: 'Microwave', running: true, center_w: 2025, wh_today: 40, runs_today: 3 },
  { id: 2, name: 'Relay', running: false, center_w: 47, wh_today: 10, runs_today: 4 },
  { id: 3, name: 'relay ', running: true, center_w: 56, wh_today: 5, runs_today: 2 },
  { id: 4, legs: 'L1', center_w: 90 },
]

describe('floor plan state', () => {
  it('derives marker state from named loads and Sense devices', () => {
    const ctx = { loadsByName: loadsIndex(patterns), senseDevices: [{ id: 'abc', name: 'Fridge', w: 140 }] }
    expect(markerState({ kind: 'load', ref: 'microwave' }, ctx)).toMatchObject({ known: true, on: true, w: 2025, whToday: 40 })
    expect(markerState({ kind: 'load', ref: 'Relay' }, ctx)).toMatchObject({ on: true, w: 56, whToday: 15, runsToday: 6 })   // grouped by name
    expect(markerState({ kind: 'load', ref: 'Kettle' }, ctx)).toMatchObject({ known: false, on: false })
    expect(markerState({ kind: 'sense', ref: 'abc' }, ctx)).toMatchObject({ on: true, w: 140 })
    expect(markerState({ kind: 'sense', ref: 'gone' }, ctx)).toMatchObject({ on: false, w: 0 })
  })
  it('assigns calm visual tiers', () => {
    expect(tier({ on: false, w: 0 })).toBe(0)
    expect(tier({ on: true, w: 8 })).toBe(1)
    expect(tier({ on: true, w: 300 })).toBe(2)
    expect(tier({ on: true, w: 1800 })).toBe(3)
  })
  it('rolls up rooms and floors from markers inside them', () => {
    const floor = { rooms: [{ id: 1, name: 'Kitchen', x: 0, y: 0, w: 0.5, h: 0.5 }],
      markers: [{ id: 1, x: 0.2, y: 0.2 }, { id: 2, x: 0.4, y: 0.1 }, { id: 3, x: 0.8, y: 0.8 }] }
    const st = { 1: { on: true, w: 2000, whToday: 40 }, 2: { on: false, w: 0, whToday: 5 }, 3: { on: true, w: 100, whToday: 1 } }
    const r = rollups(floor, m => st[m.id])
    expect(r.rooms[0]).toMatchObject({ watts: 2000, wh: 45, count: 2, on: 1, w: 0.5 })   // geometry kept
    expect(r).toMatchObject({ watts: 2100, on: 2 })
  })
})

describe('floor plan geometry', () => {
  it('converts client points to clamped normalized coordinates', () => {
    const rect = { left: 100, top: 50, width: 400, height: 200 }
    expect(toNormalized(300, 150, rect)).toEqual({ x: 0.5, y: 0.5, inside: true })
    expect(toNormalized(50, 400, rect)).toEqual({ x: 0, y: 1, inside: false })
  })
  it('builds a rectangle from any drag direction', () => {
    expect(rectFrom({ x: 0.6, y: 0.7 }, { x: 0.2, y: 0.1 })).toEqual({ x: 0.2, y: 0.1, w: 0.39999999999999997, h: 0.6 })
  })
  it('zooms around the pointer, keeping that point fixed', () => {
    const v = zoomAt({ scale: 1, x: 0, y: 0 }, 2, 100, 50)
    expect(v).toEqual({ scale: 2, x: -100, y: -50 })
    // content point under the cursor: (100 - x) / scale stays 100
    expect((100 - v.x) / v.scale).toBe(100)
    expect(zoomAt({ scale: 7, x: 0, y: 0 }, 4, 0, 0).scale).toBe(8)   // clamped
  })
  it('fits an image into its box, centred', () => {
    const v = fitView(2000, 1000, 1048, 548, 24)
    expect(v.scale).toBe(0.5)
    expect(v.x).toBe(24)
    expect(v.y).toBe(24)
  })
  it('guesses icons from names', () => {
    expect(guessIcon('Water dispenser')).toBe('water')
    expect(guessIcon('Well pump')).toBe('pump')
    expect(guessIcon('Heat Pump')).toBe('ac')
    expect(guessIcon('Mystery')).toBe('plug')
    expect(guessIcon('Relay / cycling device')).toBe('plug')   // not 'ev'
    expect(guessIcon('Space heater')).toBe('heater')            // not 'ac'
    expect(guessIcon('EV charger')).toBe('car')
    expect(guessIcon('Basement A/C')).toBe('ac')
  })
})
