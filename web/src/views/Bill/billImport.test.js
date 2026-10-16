import { describe, expect, it } from 'vitest'
import { mappingFrom, summarize } from './BillImport'

describe('import summary', () => {
  it('counts bills, usage readings and failures', () => {
    const s = summarize([
      { ok: true, data: { kind: 'bill' } }, { ok: true, data: { kind: 'bill-pdf' } },
      { ok: true, data: { kind: 'usage', added: 8000, updated: 757 } }, { ok: false, error: 'unknown' },
    ])
    expect(s).toEqual({ bills: 2, usageFiles: 1, rows: 8757, failed: 1 })
  })
  it('builds a CSV mapping from the chosen roles, never from hidden columns', () => {
    const header = ['[redacted]', 'When', 'Energy', 'Temperature']
    expect(mappingFrom(header, ['kwh', 'start', 'kwh', ''], 'kWh', 'm', 'h'.repeat(64), 'UTC'))
      .toEqual({ name: 'm', match_hash: 'h'.repeat(64), columns: { start: 'When', kwh: 'Energy' }, units: 'kWh', timezone: 'UTC' })
    expect(mappingFrom(header, ['', '', 'kwh', ''], 'kWh', 'm', 'x', 'UTC')).toBe(null)   // no time column
  })
})
