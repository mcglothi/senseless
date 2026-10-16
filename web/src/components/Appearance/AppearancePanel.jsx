import { useEffect, useRef, useState } from 'react'
import { C, MONO, alpha, useTheme } from '../../ui/theme'
import { ACCENT_SLOTS, GLOW_OPTIONS, PATTERN_OPTIONS, accentOf, tokensFor } from '../../ui/themes'
import { ThemeGallery } from './ThemeGallery'

// The ⚙ panel on Home: mode, a theme for dark and one for light, accent, radius,
// background pattern and glow. Everything saves to the Senseless server through useAppearance.
export const Label = ({ children, right }) => (
  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
    <span style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: 1, textTransform: 'uppercase' }}>{children}</span>
    {right && <span style={{ fontFamily: MONO, fontSize: 9, color: C.faint }}>{right}</span>}
  </div>
)

export function Segmented({ value, options, onChange }) {
  const { accent } = useTheme()
  return (
    <div style={{ display: 'inline-flex', border: `1px solid ${C.line2}`, borderRadius: 8, overflow: 'hidden' }}>
      {options.map(([v, label]) => {
        const on = v === value
        return (
          <button key={v} onClick={() => onChange(v)} style={{
            fontFamily: MONO, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', padding: '6px 12px',
            background: on ? alpha(accent, '20') : 'transparent', color: on ? accent : C.muted,
            border: 'none', borderRight: `1px solid ${C.line}`, cursor: 'pointer',
          }}>{label}</button>
        )
      })}
    </div>
  )
}

// A theme drawn with its own colors: page, a card, two text lines, the accents.
export function ThemeCard({ theme, selected, showing, badge, onPick, onRemove, onEnter, onLeave }) {
  const { accent } = useTheme()
  const t = tokensFor(theme)
  const p = theme.palette
  return (
    <div style={{ position: 'relative', minWidth: 0 }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <button onClick={onPick} title={`${theme.name}${theme.author ? ' — ' + theme.author : ''}`} style={{
        padding: 0, width: '100%', background: 'none', cursor: 'pointer', textAlign: 'left', borderRadius: 10,
        border: `1px solid ${selected ? accent : C.line}`,
        boxShadow: selected ? `0 0 0 1px ${accent}` : 'none', overflow: 'hidden', display: 'block',
      }}>
        <div style={{ background: t.bg, padding: 8, height: 62, display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div style={{ background: t.panel, border: `1px solid ${alpha(t.text, '17')}`, borderRadius: 5, padding: '5px 6px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ height: 4, width: '70%', borderRadius: 2, background: t.text }} />
            <div style={{ height: 4, width: '45%', borderRadius: 2, background: t.muted }} />
          </div>
          <div style={{ display: 'flex', gap: 3 }}>
            {['base08', 'base09', 'base0A', 'base0B', 'base0C', 'base0D', 'base0E'].map(k => (
              <span key={k} style={{ width: 8, height: 8, borderRadius: '50%', background: p[k] }} />
            ))}
            <span style={{ flex: 1 }} />
            <span style={{ width: 14, height: 8, borderRadius: 3, background: accentOf(theme) }} />
          </div>
        </div>
        <div style={{
          display: 'flex', gap: 6, alignItems: 'baseline', fontFamily: MONO, fontSize: 10, padding: '5px 8px',
          color: selected ? C.text : C.muted, background: C.panel, borderTop: `1px solid ${C.line}`,
        }}>
          <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{showing ? '● ' : ''}{theme.name}</span>
          {badge && <span style={{ fontSize: 8, color: C.dim, letterSpacing: 0.5, textTransform: 'uppercase', flexShrink: 0 }}>{badge}</span>}
        </div>
      </button>
      {onRemove && (
        <button onClick={onRemove} title={`Remove ${theme.name}`} style={{
          position: 'absolute', top: 4, right: 4, width: 18, height: 18, borderRadius: '50%', padding: 0,
          border: `1px solid ${C.line2}`, background: C.panel, color: C.muted, fontSize: 11, lineHeight: '16px', cursor: 'pointer',
        }}>×</button>
      )}
    </div>
  )
}

function ThemeGrid({ themes, selectedId, showing, onPick, onRemove, onHover }) {
  // Hover tries a theme on after a short pause, so sweeping across the grid doesn't flicker
  const timer = useRef(null)
  const enter = t => { clearTimeout(timer.current); timer.current = setTimeout(() => onHover?.(t), 280) }
  const leave = () => { clearTimeout(timer.current); onHover?.(null) }
  useEffect(() => () => clearTimeout(timer.current), [])
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(132px, 1fr))', gap: 8 }}>
      {themes.map(t => (
        <ThemeCard key={t.id} theme={t} selected={t.id === selectedId} showing={showing && t.id === selectedId}
          badge={t.installed ? 'added' : null} onPick={() => { clearTimeout(timer.current); onPick(t.id) }}
          onEnter={() => enter(t)} onLeave={leave}
          onRemove={t.installed ? () => { if (window.confirm(`Remove ${t.name}?`)) onRemove(t.id) } : null} />
      ))}
    </div>
  )
}

// A background layer: which kind, and how strong
function Background({ label, options, kind, strength, onKind, onStrength }) {
  return (
    <div>
      <Label right={kind === 'none' ? null : `${strength}%`}>{label}</Label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <Segmented value={kind} onChange={onKind} options={options} />
        <input type="range" min={1} max={30} value={strength} disabled={kind === 'none'}
          onChange={e => onStrength(parseInt(e.target.value))} title={`${label} strength`}
          style={{ width: 80, accentColor: C.accent, opacity: kind === 'none' ? 0.3 : 1 }} />
      </div>
    </div>
  )
}

function Swatch({ color, on, title, onClick, children }) {
  return (
    <button onClick={onClick} title={title} style={{
      width: 22, height: 22, borderRadius: '50%', background: color, border: `1px solid ${C.line2}`, padding: 0,
      cursor: 'pointer', outline: on ? `2px solid ${color}` : 'none', outlineOffset: 2,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', position: 'relative',
    }}>{children}</button>
  )
}

const Section = ({ title, right, children }) => (
  <section style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontFamily: MONO, fontSize: 10, color: C.muted, letterSpacing: 2, textTransform: 'uppercase' }}>{title}</span>
      <span style={{ flex: 1, height: 1, background: C.line }} />
      {right}
    </div>
    {children}
  </section>
)
const Row = ({ children }) => <div style={{ display: 'flex', flexWrap: 'wrap', gap: 22, alignItems: 'flex-end' }}>{children}</div>

export function AppearancePanel({ appearance }) {
  const { settings, catalog, theme, prefersLight, prefersReducedMotion, update, error, remove } = appearance
  const { accent } = useTheme()
  const [browsing, setBrowsing] = useState(false)
  const [removeError, setRemoveError] = useState(null)
  const onRemove = id => { setRemoveError(null); remove(id).catch(e => setRemoveError(e.message)) }
  if (!theme) return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>{error ? `appearance: ${error}` : 'loading themes…'}</div>
  if (browsing) return <ThemeGallery appearance={appearance} onClose={() => setBrowsing(false)} />

  const showingLight = theme.variant === 'light'
  const dark = catalog.filter(t => t.variant !== 'light')
  const light = catalog.filter(t => t.variant === 'light')
  const custom = settings.accent?.startsWith('#') ? settings.accent : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 26 }}>
      {error && <div style={{ fontFamily: MONO, fontSize: 10, color: C.red }}>{error}</div>}

      <Section title="Theme" right={
        <button onClick={() => setBrowsing(true)} style={{
          fontFamily: MONO, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', padding: '5px 10px',
          background: 'transparent', color: accent, border: `1px solid ${alpha(accent, '55')}`, borderRadius: 8, cursor: 'pointer',
        }}>Browse gallery →</button>
      }>
        <div>
          <Label right={settings.mode === 'system' ? `macOS is ${prefersLight ? 'light' : 'dark'} now` : 'Shift+L flips dark/light'}>Mode</Label>
          <Segmented value={settings.mode} onChange={mode => update({ mode })}
            options={[['dark', 'Dark'], ['light', 'Light'], ['system', 'System']]} />
        </div>
        {/* The grid follows the mode. System swaps between two themes, so it shows both.
            Hovering a card tries it on the page; clicking keeps it. */}
        {(settings.mode === 'system' ? ['dark', 'light'] : [settings.mode]).map(side => (
          <div key={side}>
            <Label right={settings.mode === 'system' ? `used while macOS is ${side}` : null}>
              {side === 'dark' ? 'Dark' : 'Light'} theme
            </Label>
            <ThemeGrid themes={side === 'dark' ? dark : light} selectedId={settings[side]}
              showing={(side === 'light') === showingLight} onRemove={onRemove}
              onHover={appearance.setPreview} onPick={id => { appearance.setPreview(null); update({ [side]: id }) }} />
          </div>
        ))}
        {removeError && <div style={{ fontFamily: MONO, fontSize: 10, color: C.red }}>couldn't remove: {removeError}</div>}
      </Section>

      <Section title="Accent">
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <Swatch color={accentOf(theme)} on={!settings.accent} title="Theme default" onClick={() => update({ accent: null })}>
            <span style={{ fontFamily: MONO, fontSize: 9, color: theme.variant === 'light' ? '#fff' : '#000', fontWeight: 700 }}>T</span>
          </Swatch>
          <span style={{ width: 1, height: 18, background: C.line2, margin: '0 2px' }} />
          {ACCENT_SLOTS.map(([slot, name]) => (
            <Swatch key={slot} color={theme.palette[slot]} on={settings.accent === slot}
              title={`${name} (follows the theme)`} onClick={() => update({ accent: slot })} />
          ))}
          <span style={{ width: 1, height: 18, background: C.line2, margin: '0 2px' }} />
          <Swatch color={custom ?? 'conic-gradient(red, yellow, lime, aqua, blue, magenta, red)'} on={!!custom} title="Custom color (same in every theme)">
            <input type="color" value={custom ?? accentOf(theme, settings.accent)}
              onChange={e => update({ accent: e.target.value.toLowerCase() })}
              style={{ position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer', width: '100%', height: '100%' }} />
          </Swatch>
          <span style={{ fontFamily: MONO, fontSize: 9, color: C.faint, marginLeft: 6 }}>
            {settings.accent ? (custom ? 'custom' : 'follows the theme') : 'theme default'}
          </span>
        </div>
      </Section>

      <Section title="Background">
        <Row>
          <Background label="Pattern" options={PATTERN_OPTIONS} kind={settings.texture} strength={settings.textureStrength}
            onKind={texture => update({ texture })} onStrength={textureStrength => update({ textureStrength })} />
          <Background label="Glow" options={GLOW_OPTIONS} kind={settings.glow} strength={settings.glowStrength}
            onKind={glow => update({ glow })} onStrength={glowStrength => update({ glowStrength })} />
        </Row>
      </Section>

      <Section title="Layout">
        <Row>
          <div>
            <Label>Density</Label>
            <Segmented value={settings.density} onChange={density => update({ density })}
              options={[['compact', 'Compact'], ['comfortable', 'Comfortable'], ['spacious', 'Spacious']]} />
          </div>
          <div>
            <Label>Text size</Label>
            <Segmented value={String(settings.textScale)} onChange={v => update({ textScale: parseInt(v) })}
              options={[['90', '90%'], ['100', '100%'], ['110', '110%'], ['120', '120%']]} />
          </div>
          <div>
            <Label>Corner radius: {settings.radius}px</Label>
            <input type="range" min={0} max={24} value={settings.radius}
              onChange={e => update({ radius: parseInt(e.target.value) })}
              style={{ width: 120, accentColor: C.accent }} />
          </div>
        </Row>
      </Section>

      <Section title="Motion">
        <div>
          <Label right={settings.motion === 'system' ? `macOS: ${prefersReducedMotion ? 'reduce motion is on' : 'full motion'}` : null}>Animations</Label>
          <Segmented value={settings.motion} onChange={motion => update({ motion })}
            options={[['system', 'Follow macOS'], ['reduced', 'Reduced'], ['full', 'Full']]} />
        </div>
      </Section>
    </div>
  )
}
