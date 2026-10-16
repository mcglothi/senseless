import { C, MONO, alpha, glow } from '../ui/theme'
import { link, useLocation } from '../router'

// Three bars: L1, L2, accent. Same mark as the favicon.
export function Mark({ size = 30 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden="true">
      <rect width="30" height="30" rx="8" fill={C.panel2} />
      <rect x="6" y="15" width="4" height="9" rx="1.5" fill={C.l1} />
      <rect x="13" y="8" width="4" height="16" rx="1.5" fill={C.l2} />
      <rect x="20" y="12" width="4" height="12" rx="1.5" fill={C.accent} />
    </svg>
  )
}

export const NAV = [
  { href: '/', label: 'Now' },
  { href: '/timeline', label: 'Timeline' },
  { href: '/loads', label: 'Loads' },
  { href: '/hunt', label: 'Hunt' },
  { href: '/history', label: 'History', soon: true },
]

function LivePill({ status, frame }) {
  const live = status.state === 'live' && status.healthy
  const color = live ? C.green : status.state === 'connecting' ? C.muted : C.amber
  const label = live ? 'Live' : status.state === 'connecting' ? 'Connecting' : status.state === 'reconnecting' ? 'Reconnecting' : 'Stale'
  return (
    <div role="status" aria-live="polite" title={live ? 'Receiving ~2 updates per second' : 'No fresh data from the Sense feed'}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: 11, color, whiteSpace: 'nowrap',
        padding: '6px 11px', borderRadius: 20, border: `1px solid ${alpha(color, '50')}`, background: alpha(color, '12'),
      }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, boxShadow: live ? `0 0 8px ${glow(color)}` : 'none',
        animation: live ? 'blink 2s ease-in-out infinite' : 'none' }} />
      {label}{live && frame ? <span style={{ color: C.dim }}>· frame {frame.toLocaleString('en-US')}</span> : null}
    </div>
  )
}

function IconButton({ label, onClick, children }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} style={{
      width: 38, height: 38, borderRadius: 10, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      background: 'transparent', color: C.muted, border: `1px solid ${C.line2}`, cursor: 'pointer', flexShrink: 0,
    }}>{children}</button>
  )
}

export function TopBar({ status, frame, isMobile, onOpenAppearance, onToggleMode, light }) {
  const { path } = useLocation()
  return (
    <header style={{
      display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', padding: isMobile ? '10px 14px' : '10px 28px',
      borderBottom: `1px solid ${alpha(C.accent, '22')}`, background: alpha(C.bg, 0.88), backdropFilter: 'blur(16px)',
      position: 'sticky', top: 0, zIndex: 10,
    }}>
      <a href="/" onClick={link('/')} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Mark />
        <span style={{ fontWeight: 700, fontSize: 17, letterSpacing: 0.4, color: C.bright }}>Senseless</span>
      </a>
      <nav aria-label="Primary" style={{ display: 'flex', gap: 4, flexWrap: 'wrap', order: isMobile ? 3 : 0, width: isMobile ? '100%' : 'auto' }}>
        {NAV.map(n => {
          const on = n.href === '/' ? path === '/' : path.startsWith(n.href)
          return (
            <a key={n.href} href={n.href} onClick={link(n.href)} aria-current={on ? 'page' : undefined}
              title={n.soon ? 'Coming soon' : undefined}
              style={{
                fontFamily: MONO, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase', padding: '8px 12px', borderRadius: 8,
                color: on ? C.accent : n.soon ? C.dim : C.muted, background: on ? alpha(C.accent, '14') : 'transparent',
                border: `1px solid ${on ? alpha(C.accent, '45') : 'transparent'}`,
              }}>{n.label}</a>
          )
        })}
      </nav>
      <span style={{ flex: 1 }} />
      {!isMobile && <LivePill status={status} frame={frame} />}
      <IconButton label={light ? 'Switch to dark (Shift+L)' : 'Switch to light (Shift+L)'} onClick={onToggleMode}>
        {light ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
        )}
      </IconButton>
      <IconButton label="Appearance" onClick={onOpenAppearance}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="13.5" cy="6.5" r="1.5" /><circle cx="17.5" cy="10.5" r="1.5" /><circle cx="8.5" cy="7.5" r="1.5" /><circle cx="6.5" cy="12.5" r="1.5" /><path d="M12 2a10 10 0 1 0 0 20c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2h2.4A5.6 5.6 0 0 0 22 9.8C22 5.5 17.5 2 12 2z" /></svg>
      </IconButton>
    </header>
  )
}
