import { useRef, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { toast } from '../../ui/primitives'

// Drop bills (PDF) and usage exports (Green Button XML/CSV, Smart Meter Texas, your utility's CSV) here.
// Each file goes up raw; the server recognises the format and keeps only dates, kWh and amounts.

const MAX = 20 * 1024 * 1024

async function upload(file) {
  if (file.size > MAX) throw new Error('over 20 MB')
  const res = await fetch('/api/billing/import', { method: 'PUT', body: file, headers: { 'X-Senseless-Upload': '1', 'Content-Type': file.type || 'application/octet-stream' } })
  const data = await res.json().catch(() => null)
  if (!res.ok) { const e = new Error(data?.error || res.statusText); e.data = data; throw e }
  return data
}

export function summarize(results) {
  const bills = results.filter(r => r.ok && r.data.kind?.includes('bill')).length
  const usage = results.filter(r => r.ok && !r.data.kind?.includes('bill'))
  const rows = usage.reduce((a, r) => a + (r.data.added ?? 0) + (r.data.updated ?? 0), 0)
  const failed = results.filter(r => !r.ok).length
  return { bills, usageFiles: usage.length, rows, failed }
}

export function BillImport({ onImported, onUnknownCsv }) {
  const { accent } = useTheme()
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(null)        // {done, total}
  const [results, setResults] = useState([])
  const input = useRef(null)

  const run = async files => {
    const list = [...files]
    if (!list.length) return
    setBusy({ done: 0, total: list.length })
    const out = []
    for (const [i, f] of list.entries()) {
      try { out.push({ name: f.name, ok: true, data: await upload(f) }) } catch (e) { out.push({ name: f.name, ok: false, error: e.message, data: e.data }) }
      setBusy({ done: i + 1, total: list.length })
    }
    setBusy(null)
    setResults(out)
    const s = summarize(out)
    toast([s.bills && `${s.bills} bill${s.bills > 1 ? 's' : ''}`, s.usageFiles && `${s.rows.toLocaleString()} usage readings`, s.failed && `${s.failed} not recognised`].filter(Boolean).join(' · ') || 'Nothing imported')
    const unknown = out.find(r => !r.ok && r.data?.header)
    if (unknown && onUnknownCsv) onUnknownCsv(unknown)
    onImported?.()
  }

  const s = results.length ? summarize(results) : null
  return (
    <div>
      <div role="button" tabIndex={0} aria-label="Import bills and usage files"
        onClick={() => input.current?.click()} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.current?.click() } }}
        onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); run(e.dataTransfer.files) }}
        style={{ border: `1.5px dashed ${over ? accent : C.line2}`, background: over ? alpha(accent, '10') : 'transparent', borderRadius: 12,
          padding: '18px 16px', textAlign: 'center', cursor: 'pointer', transition: 'all .15s' }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: C.bright, marginBottom: 4 }}>
          {busy ? `Importing ${busy.done} of ${busy.total}…` : 'Drop bills and usage exports here'}
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.6 }}>
          Bill PDFs, Green Button (XML or CSV), Smart Meter Texas, or your utility's usage CSV. Several at once is fine.
          Names, addresses and account numbers are dropped on import.
        </div>
        <input ref={input} type="file" multiple hidden accept=".pdf,.csv,.xml,application/pdf,text/csv,text/xml,application/xml"
          onChange={e => { const f = e.target.files; run(f); e.target.value = '' }} />
      </div>
      {s && (
        <div style={{ marginTop: 10, fontFamily: MONO, fontSize: 11, color: C.body, lineHeight: 1.7 }}>
          {s.bills > 0 && <div>✓ {s.bills} bill{s.bills > 1 ? 's' : ''} imported</div>}
          {s.usageFiles > 0 && <div>✓ {s.rows.toLocaleString()} usage readings from {s.usageFiles} file{s.usageFiles > 1 ? 's' : ''}</div>}
          {results.filter(r => !r.ok).map(r => (
            <div key={r.name} style={{ color: C.amber }}>✗ {r.name}: {r.error}</div>
          ))}
        </div>
      )}
    </div>
  )
}
