import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/api'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { Segmented, ThemeCard } from './AppearancePanel'

// Every scheme in tinted-theming/schemes, from the Senseless server's cached copy.
// Clicking a card tries it on the whole page; "Use" adds it to your themes and
// switches to it. Nothing is saved until then.
export function ThemeGallery({ appearance, onClose }) {
  const { settings, catalog, update, preview, setPreview, install } = appearance
  const { accent } = useTheme()
  const [gallery, setGallery] = useState(null)
  const [error, setError] = useState(null)
  const [query, setQuery] = useState('')
  const [variant, setVariant] = useState('all')
  const [busy, setBusy] = useState(false)

  const load = refresh => {
    setError(null)
    api('GET', `/api/themes/gallery${refresh ? '?refresh=1' : ''}`)
      .then(d => { setGallery(d); if (d.error) setError(d.error) })
      .catch(e => setError(e.message))
  }
  useEffect(() => { load(false) }, [])

  // Leaving the gallery (or Esc) puts your own theme back
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') setPreview(null) }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); setPreview(null) }
  }, [setPreview])

  const owned = useMemo(() => new Map(catalog.map(t => [t.id, t.installed ? 'added' : 'bundled'])), [catalog])
  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean)
    return (gallery?.themes ?? []).filter(t =>
      (variant === 'all' || t.variant === variant) &&
      words.every(w => `${t.name} ${t.id} ${t.author ?? ''}`.toLowerCase().includes(w)))
  }, [gallery, query, variant])

  async function use() {
    const t = preview
    const side = t.variant === 'light' ? 'light' : 'dark'
    setBusy(true)
    try {
      if (!owned.has(t.id)) await install(t.id)
      const flip = side === 'light' ? settings.mode === 'dark' : settings.mode === 'light'
      update({ [side]: t.id, ...(flip ? { mode: side } : {}) })
      setPreview(null)
      onClose()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function add() {
    setBusy(true)
    try { await install(preview.id); setPreview(null) } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const btn = primary => ({
    fontFamily: MONO, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', padding: '6px 12px', borderRadius: 8,
    cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.5 : 1,
    background: primary ? alpha(accent, '20') : 'transparent', color: primary ? accent : C.muted,
    border: `1px solid ${primary ? alpha(accent, '70') : C.line2}`,
  })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <button onClick={onClose} style={btn(false)}>← Your themes</button>
        <input autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="search 500 schemes…"
          style={{
            fontFamily: MONO, fontSize: 11, color: C.text, background: C.field, border: `1px solid ${C.line2}`,
            borderRadius: 8, padding: '6px 10px', width: 220, outline: 'none',
          }} />
        <Segmented value={variant} onChange={setVariant} options={[['all', 'All'], ['dark', 'Dark'], ['light', 'Light']]} />
        <span style={{ fontFamily: MONO, fontSize: 9, color: C.faint }}>
          {gallery ? `${shown.length} of ${gallery.themes.length}` : 'loading…'}
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={() => load(true)} title="Download the scheme list again" style={{ ...btn(false), padding: '4px 8px' }}>↻</button>
        <a href="https://github.com/tinted-theming/schemes" target="_blank" rel="noreferrer"
          style={{ fontFamily: MONO, fontSize: 9, color: C.faint }}>tinted-theming/schemes</a>
      </div>

      {preview && (
        <div style={{
          position: 'sticky', top: -18, zIndex: 2, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
          padding: '8px 12px', marginBottom: 10, borderRadius: 10, background: C.panel2, border: `1px solid ${alpha(accent, '55')}`,
        }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text }}>
            Trying <b>{preview.name}</b> <span style={{ color: C.dim }}>· {preview.variant}{preview.author ? ` · ${preview.author}` : ''}</span>
          </span>
          <span style={{ flex: 1 }} />
          <button disabled={busy} onClick={use} style={btn(true)}>Use as {preview.variant === 'light' ? 'light' : 'dark'} theme</button>
          {!owned.has(preview.id) && <button disabled={busy} onClick={add} style={btn(false)}>Add to my themes</button>}
          <button onClick={() => setPreview(null)} style={btn(false)}>Cancel · esc</button>
        </div>
      )}

      {error && <div style={{ fontFamily: MONO, fontSize: 10, color: C.amber, marginBottom: 8 }}>{error}</div>}

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(132px, 1fr))', gap: 8,
      }}>
        {shown.map(t => (
          <ThemeCard key={t.id} theme={t} selected={preview?.id === t.id} badge={owned.get(t.id) ?? (t.system === 'base24' ? 'b24' : null)}
            onPick={() => setPreview(preview?.id === t.id ? null : t)} />
        ))}
      </div>
    </div>
  )
}
