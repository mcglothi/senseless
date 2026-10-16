// base16 palette → the page's color tokens (see :root in index.html).
//
// base16 schemes agree on what base00 (background), base05 (text) and base08-0F
// (red, orange, yellow, green, cyan, blue, magenta, brown) mean, but not on the
// surface slots: base01/02 are darker than the background in some schemes and
// nearly text-colored in others. So surfaces and the grey text ladder are mixed
// from background and text, which keeps contrast the same in every theme, and the
// accents come straight from the scheme.

export const ACCENT_SLOTS = [
  ['base08', 'Red'], ['base09', 'Orange'], ['base0A', 'Yellow'], ['base0B', 'Green'],
  ['base0C', 'Cyan'], ['base0D', 'Blue'], ['base0E', 'Magenta'], ['base0F', 'Brown'],
]

const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
const hex = c => '#' + c.map(v => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')
// a moved t of the way toward b
export const mix = (a, b, t) => { const A = rgb(a), B = rgb(b); return hex(A.map((v, i) => v + (B[i] - v) * t)) }
export const lum = h => { const [r, g, b] = rgb(h).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }); return 0.2126 * r + 0.7152 * g + 0.0722 * b }

const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05) }

// Some schemes' accents are too faint on their own background (a yellow on cream).
// Nudge a color toward the text color only as far as it takes to reach 3:1 against
// both the page and the cards, so every theme keeps its character but stays legible.
export function legible(color, surfaces, fg, min = 3) {
  let c = color
  for (let t = 0.05; t <= 0.6 && surfaces.some(s => contrast(c, s) < min); t += 0.05) c = mix(color, fg, t)
  return c
}

// Hue (0..360) and saturation (0..1) of a hex color.
export function hueSat(h) {
  const [r, g, b] = rgb(h).map(v => v / 255)
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2
  if (!d) return { h: 0, s: 0 }
  const s = d / (1 - Math.abs(2 * l - 1))
  const hue = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
  return { h: (hue * 60 + 360) % 360, s }
}

// Leg colors by meaning, not just slot: L2 is the first conventional slot that really is a
// saturated warm color, L1 the first that is a saturated cool one. Not every scheme puts
// orange in base09 (Tokyo Night's base09 is a grey-blue foreground).
export function legColors(p, light) {
  const pick = (slots, inRange, fallback) => {
    const c = slots.map(k => p[k]).filter(Boolean).map(hex => ({ hex, ...hueSat(hex) }))
      .find(x => x.s >= 0.35 && inRange(x.h))
    return c ? c.hex : fallback
  }
  const warm = h => h <= 55 || h >= 330
  const cool = h => h >= 165 && h <= 250
  let l2 = pick(['base09', 'base08', 'base0A', 'base0F'], warm, null)
  let l1 = pick(light ? ['base0D', 'base0C'] : ['base0C', 'base0D'], cool, null)
  // No proper warm/cool slot (muted or odd schemes): take the most saturated accent left,
  // preferring the hue furthest from the other leg so the two stay distinguishable.
  const all = ['base08', 'base09', 'base0A', 'base0B', 'base0C', 'base0D', 'base0E', 'base0F']
    .map(k => p[k]).filter(Boolean).map(hex => ({ hex, ...hueSat(hex) }))
  const far = (other, x) => (other ? Math.min(Math.abs(x.h - hueSat(other).h), 360 - Math.abs(x.h - hueSat(other).h)) / 180 : 0)
  const best = other => all.filter(x => x.hex !== other).sort((a, b) => (b.s + far(other, b)) - (a.s + far(other, a)))[0]?.hex
  if (!l2) l2 = best(l1) ?? p.base09
  if (!l1) l1 = best(l2) ?? (light ? p.base0D : p.base0C)
  return { l1, l2 }
}

export function accentOf(theme, pref) {
  const p = theme.palette
  if (pref && pref.startsWith('#')) return pref
  return p[pref || theme.accent || 'base0E']
}

export function tokensFor(theme, accentPref) {
  const p = theme.palette
  const light = theme.variant === 'light'
  const bg = p.base00, fg = p.base05
  const toward = t => mix(bg, fg, t)
  // Dark: cards sit a step above the page. Light: the page drops a step and cards
  // and fields stay at the scheme's background, the usual light-UI arrangement.
  const surfaces = light
    ? { bg: toward(0.045), field: bg, panel: bg, panel2: toward(0.03) }
    : { bg, field: toward(0.013), panel: toward(0.04), panel2: toward(0.067) }
  const brighter = light ? lum(p.base07) < lum(fg) : lum(p.base07) > lum(fg)
  const ok = c => legible(c, [surfaces.bg, surfaces.panel], fg)
  return {
    scheme: light ? 'light' : 'dark',
    ...surfaces,
    bright: brighter ? p.base07 : mix(fg, light ? '#000000' : '#ffffff', 0.5),
    text: fg,
    body: mix(fg, bg, 0.14),
    muted: mix(fg, bg, light ? 0.36 : 0.42),
    dim: mix(fg, bg, light ? 0.56 : 0.66),
    faint: mix(fg, bg, light ? 0.74 : 0.81),
    red: ok(p.base08), orange: ok(p.base09), amber: ok(p.base0A), green: ok(p.base0B),
    cyan: ok(p.base0C), blue: ok(p.base0D), violet: ok(p.base0E),
    accent: ok(accentOf(theme, accentPref)),
    // Leg identity colors: L1 cyan (blue on light themes, where cyan washes out), L2 orange.
    l1: ok(legColors(p, light).l1), l2: ok(legColors(p, light).l2),
  }
}

// Which theme is showing: mode is dark | light | system.
export function activeTheme(settings, catalog, prefersLight) {
  const light = settings.mode === 'light' || (settings.mode === 'system' && prefersLight)
  const id = light ? settings.light : settings.dark
  return catalog.find(t => t.id === id) ?? catalog.find(t => t.id === (light ? 'senseless-light' : 'senseless')) ?? catalog[0]
}

export const CACHE_KEY = 'senseless.appearance.tokens'

// Writes the tokens onto <html> and remembers them, so the inline script in
// index.html can paint the right colors before React and the server answer.
// Page background, two layers under the app (index.html): body::before draws the
// pattern in --dots (the text color at the pattern strength), body::after the glow.
// Crosshairs need short strokes a gradient can't draw, so they're an SVG mask over
// a solid --dots fill.
const CROSS_MASK = `url("data:image/svg+xml,${encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='28' height='28'><rect x='13' y='9' width='1' height='10'/><rect x='9' y='13' width='10' height='1'/></svg>")}")`
const DOTS = 'var(--dots)'
const PATTERNS = {
  none: { image: 'none' },
  dots: { image: `radial-gradient(circle, ${DOTS} 1.2px, transparent 1.6px)`, size: '28px 28px' },
  grid: { image: `linear-gradient(${DOTS} 1px, transparent 1px), linear-gradient(90deg, ${DOTS} 1px, transparent 1px)`, size: '28px 28px' },
  crosshair: { image: `linear-gradient(${DOTS}, ${DOTS})`, size: '28px 28px', mask: CROSS_MASK },
  scanlines: { image: `repeating-linear-gradient(${DOTS} 0 1px, transparent 1px 4px)` },
  hatch: { image: `repeating-linear-gradient(45deg, ${DOTS} 0 1px, transparent 1px 10px)` },
}
export const PATTERN_OPTIONS = [['none', 'None'], ['dots', 'Dots'], ['grid', 'Grid'], ['crosshair', 'Cross'], ['scanlines', 'Scan'], ['hatch', 'Hatch']]
export const GLOW_OPTIONS = [['none', 'None'], ['corners', 'Corners'], ['vignette', 'Vignette'], ['aurora', 'Aurora']]

const tint = (color, pct) => `color-mix(in srgb, ${color} ${pct}%, transparent)`
function glowImage(kind, s, scheme) {
  if (kind === 'corners') return [
    // the two accent blooms the page always had (strength 3 is the original look)
    `radial-gradient(circle 346px at 150px 150px, ${tint('var(--accent)', s)}, transparent)`,
    `radial-gradient(circle 297px at calc(100% - 120px) calc(100% - 120px), ${tint('var(--accent)', s * 0.9)}, transparent)`,
  ].join(', ')
  if (kind === 'vignette') {
    const ink = scheme === 'light' ? 'var(--text)' : '#000000'
    return `radial-gradient(ellipse at center, transparent 45%, ${tint(ink, Math.min(90, s * 2.5))} 100%)`
  }
  if (kind === 'aurora') return [
    `radial-gradient(ellipse 55% 45% at 12% 8%, ${tint('var(--violet)', s)}, transparent 70%)`,
    `radial-gradient(ellipse 45% 55% at 88% 28%, ${tint('var(--cyan)', s * 0.8)}, transparent 70%)`,
    `radial-gradient(ellipse 70% 45% at 45% 105%, ${tint('var(--accent)', s)}, transparent 70%)`,
  ].join(', ')
  return 'none'
}

function background({ texture = 'dots', textureStrength = 8, glow = 'corners', glowStrength = 3 }, scheme) {
  const p = PATTERNS[texture] ?? PATTERNS.none
  return {
    dots: tint('var(--text)', textureStrength),
    'texture-image': p.image,
    'texture-size': p.size ?? 'auto',
    'texture-mask': p.mask ?? 'none',
    'glow-image': glowImage(glow, glowStrength, scheme),
  }
}

export function applyTokens(tokens, settings, { cache = true } = {}) {
  const root = document.documentElement
  const { scheme, ...colors } = tokens
  const vars = {
    ...colors, ...background(settings, scheme), 'r-card': `${settings.radius}px`,
    // Colored glows (shadows) are a neon, dark-UI effect; on light themes they read
    // as smudges, so glow() shadows drop to a quarter there.
    'glow-k': scheme === 'light' ? 0.25 : 1,
    // Drop shadows: deep on dark themes, a soft lift on light ones
    shade: scheme === 'light' ? 'rgba(0,0,0,0.12)' : 'rgba(0,0,0,0.6)',
    scale: (settings.textScale ?? 100) / 100,
  }
  // Reduced motion also stills the glows' neon feel a little
  if (settings.reducedMotion) vars['glow-k'] = scheme === 'light' ? 0.2 : 0.6
  const motion = settings.reducedMotion ? 'reduced' : 'full'
  root.dataset.motion = motion
  const scaleChanged = root.style.getPropertyValue('--scale') !== String(vars.scale)
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(`--${k}`, v)
  if (scaleChanged) window.dispatchEvent(new Event('senseless:scale'))
  root.style.colorScheme = scheme
  root.dataset.theme = scheme
  if (!cache) return
  setFavicon(tokens)
  try { localStorage.setItem(CACHE_KEY, JSON.stringify({ scheme, motion, ...vars })) } catch { /* private window */ }
}

// The tab icon is the Senseless mark (three bars: L1, L2, accent) in the theme's own colors
function setFavicon(t) {
  const bar = (x, y, h, color) => `<rect x='${x}' y='${y}' width='5' height='${h}' rx='1.6' fill='${color}'/>`
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='${t.panel2}'/>` +
    bar(6, 16, 10, t.l1) + bar(13.5, 8, 18, t.l2) + bar(21, 12, 14, t.accent) + '</svg>'
  let link = document.querySelector("link[rel='icon']")
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link) }
  link.href = 'data:image/svg+xml,' + encodeURIComponent(svg)
}
