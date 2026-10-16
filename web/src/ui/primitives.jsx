import { useEffect, useState } from 'react'
import { C, MONO, useTheme, alpha, glow } from './theme'

// Console-look building blocks shared by every view. Keep them dumb: layout and
// colour only, no data fetching.

export function SectionTitle({ children, right, style }) {
  const { accent } = useTheme()
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 0 12px', ...style }}>
      <span style={{ fontFamily: MONO, fontSize: 10, color: accent, letterSpacing: 3, textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
        {children}
      </span>
      <span style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${alpha(accent, '40')}, transparent)` }} />
      {right}
    </div>
  )
}

export function Panel({ children, color, active, style, ...rest }) {
  const { cardRadius, accent } = useTheme()
  const c = color || accent
  return (
    <div {...rest} style={{
      background: active ? `linear-gradient(${alpha(c, '14')}, ${alpha(c, '14')}), ${C.panel}` : C.panel,
      border: `1px solid ${active ? alpha(c, '70') : alpha(c, '30')}`,
      borderRadius: cardRadius, boxShadow: active ? `0 0 18px ${glow(c, '22')}` : 'none',
      transition: 'border-color .15s, box-shadow .15s, background .15s', ...style,
    }}>{children}</div>
  )
}

export function Button({ children, variant = 'default', size = 'md', color, style, ...rest }) {
  const { accent } = useTheme()
  const c = color || accent
  const [hover, setHover] = useState(false)
  const pad = size === 'sm' ? '3px 9px' : '6px 13px'
  const base = {
    fontFamily: MONO, fontSize: size === 'sm' ? 10 : 11, letterSpacing: 1, textTransform: 'uppercase',
    padding: pad, borderRadius: 6, cursor: rest.disabled ? 'default' : 'pointer', whiteSpace: 'nowrap',
    display: 'inline-flex', alignItems: 'center', gap: 6, opacity: rest.disabled ? 0.45 : 1,
    transition: 'all .15s',
  }
  const looks = {
    default: { background: hover ? C.panel2 : 'transparent', border: `1px solid ${hover ? C.line2 : C.line}`, color: C.body },
    primary: { background: hover ? `${alpha(c, '30')}` : `${alpha(c, '1c')}`, border: `1px solid ${alpha(c, '80')}`, color: c, boxShadow: hover ? `0 0 12px ${glow(c, '40')}` : 'none' },
    ghost: { background: hover ? C.panel2 : 'transparent', border: '1px solid transparent', color: hover ? C.text : C.muted },
    danger: { background: hover ? `${alpha(C.red, '1c')}` : 'transparent', border: `1px solid ${alpha(C.red, '55')}`, color: C.red },
  }
  return (
    <button {...rest} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ ...base, ...looks[variant], ...style }}>{children}</button>
  )
}

export function Pill({ children, color = C.muted, solid, title, style, onClick }) {
  return (
    <span title={title} onClick={onClick} style={{
      fontFamily: MONO, fontSize: 10, color, padding: '2px 8px', borderRadius: 20,
      border: `1px solid ${alpha(color, '55')}`, background: solid ? `${alpha(color, '18')}` : 'transparent',
      display: 'inline-flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap',
      cursor: onClick ? 'pointer' : 'default', ...style,
    }}>{children}</span>
  )
}

export function Dot({ color, pulse, size = 7, title }) {
  return <span title={title} style={{ width: size, height: size, borderRadius: '50%', background: color, flexShrink: 0,
    boxShadow: pulse ? `0 0 8px ${glow(color)}` : 'none', display: 'inline-block' }} />
}

export function Label({ children, style }) {
  return <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: 1.5, textTransform: 'uppercase', marginBottom: 6, ...style }}>{children}</div>
}

const fieldStyle = accent => ({
  width: '100%', background: C.field, border: `1px solid ${C.line2}`, borderRadius: 8,
  color: C.text, fontFamily: MONO, fontSize: 12, padding: '8px 11px', outline: 'none', caretColor: accent,
  resize: 'vertical',
})

export function TextInput({ style, ...rest }) {
  const { accent } = useTheme()
  const [focus, setFocus] = useState(false)
  return <input {...rest} onFocus={e => { setFocus(true); rest.onFocus?.(e) }} onBlur={e => { setFocus(false); rest.onBlur?.(e) }}
    style={{ ...fieldStyle(accent), borderColor: focus ? alpha(accent, '80') : C.line2, ...style }} />
}

export function TextArea({ style, ...rest }) {
  const { accent } = useTheme()
  const [focus, setFocus] = useState(false)
  return <textarea {...rest} onFocus={e => { setFocus(true); rest.onFocus?.(e) }} onBlur={e => { setFocus(false); rest.onBlur?.(e) }}
    style={{ ...fieldStyle(accent), borderColor: focus ? alpha(accent, '80') : C.line2, lineHeight: 1.5, ...style }} />
}

export function Select({ style, children, ...rest }) {
  const { accent } = useTheme()
  return <select {...rest} style={{ ...fieldStyle(accent), width: 'auto', ...style }}>{children}</select>
}

// One toast at a time, bottom-centre. toast('Saved') from anywhere.
let pushToast = () => {}
export const toast = msg => pushToast(msg)
export function Toaster() {
  const { accent } = useTheme()
  const [msg, setMsg] = useState('')
  useEffect(() => {
    let t
    pushToast = m => { setMsg(m); clearTimeout(t); t = setTimeout(() => setMsg(''), 2200) }
    return () => { pushToast = () => {} }
  }, [])
  return (
    <div style={{
      position: 'fixed', bottom: 22, left: '50%', transform: 'translateX(-50%)', zIndex: 100,
      fontFamily: MONO, fontSize: 11, color: accent, background: alpha(C.field, 'ee'), border: `1px solid ${alpha(accent, '55')}`,
      boxShadow: `0 0 20px ${glow(accent, '30')}`, padding: '8px 14px', borderRadius: 8, pointerEvents: 'none',
      opacity: msg ? 1 : 0, transition: 'opacity .2s',
    }}>{msg}</div>
  )
}

export function Empty({ title, children }) {
  return (
    <div style={{ textAlign: 'center', padding: '56px 20px', fontFamily: MONO }}>
      <div style={{ color: C.muted, fontSize: 13, marginBottom: 8 }}>{title}</div>
      {children && <div style={{ color: C.dim, fontSize: 11, maxWidth: 460, margin: '0 auto', lineHeight: 1.6 }}>{children}</div>}
    </div>
  )
}
