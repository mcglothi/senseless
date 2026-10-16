import { useRef, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { toast } from '../../ui/primitives'

// Drop bills (PDF) and usage exports (Green Button XML/CSV, Smart Meter Texas, your utility's CSV) here.
// Each file goes up raw; the server recognises the format and keeps only dates, kWh and amounts.

const MAX = 20 * 1024 * 1024

async function preview(file) {
  const res = await fetch('/api/billing/import/preview', { method: 'POST', body: file, headers: { 'X-Senseless-Upload': '1', 'Content-Type': file.type || 'application/octet-stream' } })
  return res.ok ? res.json() : null
}

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

export function BillImport({ onImported }) {
  const { accent } = useTheme()
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(null)        // {done, total}
  const [results, setResults] = useState([])
  const [mapping, setMapping] = useState(null)   // {file, header, header_hash} for a CSV nobody recognises
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
    // a CSV no importer recognised: offer to map its columns once
    const unknownIdx = out.findIndex(r => !r.ok && /unrecognized/i.test(r.error))
    if (unknownIdx >= 0 && /\.(csv|txt)$/i.test(list[unknownIdx].name)) {
      const p = await preview(list[unknownIdx])
      if (p?.header_hash) setMapping({ file: list[unknownIdx], header: p.header, header_hash: p.header_hash })
    }
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
      {mapping && <CsvMapper m={mapping} onCancel={() => setMapping(null)} onSaved={async () => { const f = mapping.file; setMapping(null); await run([f]) }} />}
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

const ROLES = [['', 'Ignore'], ['start', 'Start time'], ['date', 'Date'], ['time', 'Time of day'], ['kwh', 'Energy'], ['temp_f', 'Temperature °F']]

export function mappingFrom(header, roles, units, name, hash, timezone) {
  const columns = {}
  header.forEach((h, i) => { if (roles[i] && h !== '[redacted]') columns[roles[i]] = h })
  const ok = columns.kwh && (columns.start || (columns.date && columns.time))
  return ok ? { name, match_hash: hash, columns, units, timezone } : null
}

function CsvMapper({ m, onCancel, onSaved }) {
  const { accent } = useTheme()
  const guess = h => (/kwh|energy|usage|consumption|value/i.test(h) ? 'kwh' : /temp|weather/i.test(h) ? 'temp_f' : /start|begin|from|timestamp|when/i.test(h) ? 'start' : /^date$|day/i.test(h) ? 'date' : /^time$/i.test(h) ? 'time' : '')
  const [roles, setRoles] = useState(() => {
    const r = m.header.map(h => (h === '[redacted]' ? '' : guess(h)))
    return r.map((x, i) => (x && r.indexOf(x) !== i ? '' : x))   // each role once
  })
  const [units, setUnits] = useState('kWh')
  const [name, setName] = useState(() => (m.file.name.replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9_-]+/g, '-').slice(0, 40) || 'my-meter'))
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const profile = mappingFrom(m.header, roles, units, name, m.header_hash, tz)
  const save = async () => {
    try {
      const res = await fetch('/api/billing/csv-profiles', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(profile) })
      const data = await res.json().catch(() => null)
      if (!res.ok) throw new Error(data?.error || res.statusText)
      toast('Mapping saved; importing…')
      onSaved()
    } catch (e) { toast(e.message) }
  }
  const sel = { height: 34, background: C.field, border: `1px solid ${C.line2}`, borderRadius: 7, color: C.text, fontFamily: MONO, fontSize: 12, padding: '0 6px' }
  return (
    <div role="dialog" aria-label="Map CSV columns" style={{ marginTop: 12, padding: 14, borderRadius: 12, border: `1px solid ${alpha(accent, '60')}`, background: alpha(accent, '08'), display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: C.bright }}>Teach Senseless this CSV ({m.file.name})</div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, lineHeight: 1.6 }}>
        Say which column holds what; files with the same columns will import automatically from now on. Columns that look like names,
        addresses or account numbers are hidden and never stored. Times without an offset are read as {tz}.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 8 }}>
        {m.header.map((h, i) => (
          <label key={i} style={{ display: 'flex', flexDirection: 'column', gap: 3, fontFamily: MONO, fontSize: 11, color: h === '[redacted]' ? C.dim : C.body }}>
            {h === '[redacted]' ? `Column ${i + 1} (hidden)` : h}
            <select value={roles[i]} disabled={h === '[redacted]'} style={sel}
              onChange={e => setRoles(r => r.map((x, k) => (k === i ? e.target.value : x === e.target.value && e.target.value ? '' : x)))}>
              {ROLES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 3, fontFamily: MONO, fontSize: 11, color: C.body }}>Energy is in
          <select value={units} onChange={e => setUnits(e.target.value)} style={sel}><option>kWh</option><option>Wh</option></select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 3, fontFamily: MONO, fontSize: 11, color: C.body }}>Mapping name
          <input value={name} onChange={e => setName(e.target.value.replace(/[^a-zA-Z0-9_-]/g, '-').slice(0, 64))} style={{ ...sel, width: 180 }} />
        </label>
        <button type="button" disabled={!profile} onClick={save} style={{ ...sel, cursor: profile ? 'pointer' : 'default', color: C.bg, background: profile ? accent : C.line2, border: 'none', padding: '0 14px', fontWeight: 700 }}>Save and import</button>
        <button type="button" onClick={onCancel} style={{ ...sel, cursor: 'pointer', padding: '0 12px' }}>Cancel</button>
      </div>
      {!profile && <div style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>Pick an Energy column and either a Start time, or a Date and a Time of day.</div>}
    </div>
  )
}
