import { describe, expect, it } from 'vitest'
import { summarize } from './BillImport'

describe('import summary', () => {
  it('counts bills, usage readings and failures', () => {
    const s = summarize([
      { ok: true, data: { kind: 'bill' } }, { ok: true, data: { kind: 'bill-pdf' } },
      { ok: true, data: { kind: 'usage', added: 8000, updated: 757 } }, { ok: false, error: 'unknown' },
    ])
    expect(s).toEqual({ bills: 2, usageFiles: 1, rows: 8757, failed: 1 })
  })
})
