import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/api'
import { activeTheme, applyTokens, tokensFor } from '../ui/themes'

// Appearance lives on the Senseless server (/api/appearance), so it follows you to
// any browser. Changes apply at once and save in the background; the page also
// re-reads them when the tab regains focus, to pick up a change made elsewhere.
const DEFAULTS = { mode: 'dark', dark: 'senseless', light: 'senseless-light', accent: null, radius: 12,
  texture: 'dots', textureStrength: 8, glow: 'corners', glowStrength: 3, density: 'comfortable', textScale: 100, motion: 'system' }
export const APPEARANCE_DEFAULTS = DEFAULTS
const prefersLightQuery = () => window.matchMedia('(prefers-color-scheme: light)')
const reducedMotionQuery = () => window.matchMedia('(prefers-reduced-motion: reduce)')

export function useAppearance() {
  const [settings, setSettings] = useState(DEFAULTS)
  const [catalog, setCatalog] = useState([])
  const [prefersLight, setPrefersLight] = useState(() => prefersLightQuery().matches)
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(() => reducedMotionQuery().matches)
  const [error, setError] = useState(null)

  // A local change made while a load is in flight wins: the load's answer predates it.
  const edits = useRef(0)
  const load = useCallback(async () => {
    const seq = edits.current
    const saved = await api('GET', '/api/appearance')
    if (edits.current !== seq) return
    setSettings(saved)
  }, [])

  useEffect(() => {
    const themes = () => api('GET', '/api/themes').then(d => setCatalog(d.themes))
    themes().catch(() => new Promise(r => setTimeout(r, 800)).then(themes)).catch(e => setError(e.message))
    // one retry: a reset connection at page load shouldn't leave the defaults showing
    load().catch(() => new Promise(r => setTimeout(r, 800)).then(load)).catch(e => setError(e.message))
    const onFocus = () => { if (!Object.keys(pending.current).length) load().catch(() => {}) }
    window.addEventListener('focus', onFocus)
    const mq = prefersLightQuery(), rm = reducedMotionQuery()
    const onScheme = e => setPrefersLight(e.matches)
    const onMotion = e => setPrefersReducedMotion(e.matches)
    mq.addEventListener('change', onScheme)
    rm.addEventListener('change', onMotion)
    return () => {
      window.removeEventListener('focus', onFocus)
      mq.removeEventListener('change', onScheme)
      rm.removeEventListener('change', onMotion)
    }
  }, [load])

  const theme = catalog.length ? activeTheme(settings, catalog, prefersLight) : null
  // A gallery theme being tried on: shown on the whole page, never saved or cached
  const [preview, setPreview] = useState(null)

  const reducedMotion = settings.motion === 'reduced' || (settings.motion === 'system' && prefersReducedMotion)

  // A committed change of theme or accent morphs every color over a quarter second
  // ([data-switching] in index.html). Previews switch instantly, so sweeping across
  // theme cards stays snappy. Other settings (radius, texture...) apply directly.
  const last = useRef({ look: null, preview: false })
  const morphTimer = useRef(null)
  useEffect(() => {
    const shown = preview ?? theme
    if (!shown) return
    const look = `${shown.id}|${settings.accent}`
    const morph = last.current.look && last.current.look !== look && !preview && !last.current.preview && !reducedMotion
    last.current = { look, preview: !!preview }
    const root = document.documentElement
    if (morph) {
      root.dataset.switching = ''
      clearTimeout(morphTimer.current)
      morphTimer.current = setTimeout(() => delete root.dataset.switching, 320)
    }
    applyTokens(tokensFor(shown, settings.accent), { ...settings, reducedMotion }, { cache: !preview })
  }, [theme, preview, settings, reducedMotion])

  // Gallery: install copies a scheme into the server's catalog; remove drops it
  // (settings that used it fall back to the default themes, server-side).
  const install = useCallback(async id => setCatalog((await api('POST', '/api/themes/install', { id })).themes), [])
  const remove = useCallback(async id => {
    setCatalog((await api('DELETE', `/api/themes/${encodeURIComponent(id)}`)).themes)
    await load()
  }, [load])

  // Saves are batched for a moment, so dragging the radius slider is one write.
  const pending = useRef({})
  const timer = useRef(null)
  const update = useCallback(patch => {
    edits.current += 1
    setSettings(s => ({ ...s, ...patch }))
    setError(null)
    pending.current = { ...pending.current, ...patch }
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      const body = pending.current
      pending.current = {}
      api('POST', '/api/appearance', body).catch(e => { setError(`couldn't save: ${e.message}`); load().catch(() => {}) })
    }, 300)
  }, [load])

  const reset = useCallback(() => update({ ...DEFAULTS }), [update])

  // Quick flip between dark and light (top bar button, Shift+L). From System it
  // switches to the opposite of what's showing now.
  const toggleMode = useCallback(() => {
    const light = settings.mode === 'light' || (settings.mode === 'system' && prefersLight)
    update({ mode: light ? 'dark' : 'light' })
  }, [settings.mode, prefersLight, update])

  return {
    settings, catalog, theme, prefersLight, prefersReducedMotion, reducedMotion, update, reset, toggleMode,
    error, preview, setPreview, install, remove,
  }
}
