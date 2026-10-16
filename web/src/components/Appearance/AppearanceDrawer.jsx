import { useEffect } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { AppearancePanel } from './AppearancePanel'

// The Appearance panel as a slide-out drawer, reachable from the top bar on every
// page. Esc first cancels a gallery preview, then closes.
export function AppearanceDrawer({ open, onClose, appearance }) {
  const { accent } = useTheme()
  const { preview, setPreview, reset } = appearance

  useEffect(() => {
    if (!open) return
    const onKey = e => {
      if (e.key !== 'Escape') return
      if (preview) setPreview(null)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, preview, setPreview, onClose])

  return (
    <>
      <div onClick={onClose} aria-hidden="true" style={{
        position: 'fixed', inset: 0, zIndex: 40, background: alpha(C.bg, 0.35),
        opacity: open ? 1 : 0, pointerEvents: open ? 'auto' : 'none', transition: 'opacity .2s',
      }} />
      <aside role="dialog" aria-label="Appearance" aria-hidden={!open} style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 41, width: 'min(640px, 100vw)',
        background: C.panel, borderLeft: `1px solid ${alpha(accent, '40')}`, boxShadow: '-16px 0 48px var(--shade)',
        transform: open ? 'none' : 'translateX(100%)', transition: 'transform .25s cubic-bezier(.2,.8,.2,1)',
        display: 'flex', flexDirection: 'column', visibility: open ? 'visible' : 'hidden',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px', borderBottom: `1px solid ${C.line}`, flexShrink: 0,
        }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: accent, letterSpacing: 3, textTransform: 'uppercase' }}>Appearance</span>
          <span style={{ flex: 1 }} />
          <button onClick={() => { if (window.confirm('Reset all appearance settings to the defaults? Added themes stay installed.')) reset() }}
            style={{
              fontFamily: MONO, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', padding: '5px 10px', borderRadius: 6,
              background: 'transparent', color: C.muted, border: `1px solid ${C.line2}`, cursor: 'pointer',
            }}>Reset to defaults</button>
          <button onClick={onClose} title="Close (Esc)" style={{
            width: 28, height: 28, borderRadius: 6, border: `1px solid ${C.line2}`, background: 'transparent',
            color: C.muted, cursor: 'pointer', fontSize: 15, lineHeight: 1,
          }}>×</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 20px 28px' }}>
          {open && <AppearancePanel appearance={appearance} />}
        </div>
      </aside>
    </>
  )
}
