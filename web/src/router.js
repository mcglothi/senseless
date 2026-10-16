import { useEffect, useState } from 'react'

// Tiny pathname router. Senseless serves this app at /, /timeline, /loads ..., so a
// full reload on any of them lands on the right view; in-app links use navigate()
// to switch views without reloading (and without dropping the live stream).
const listeners = new Set()

export function navigate(href, { replace = false } = {}) {
  const url = new URL(href, window.location.origin)
  if (url.origin !== window.location.origin) { window.location.href = href; return }
  if (url.pathname + url.search === window.location.pathname + window.location.search) return
  window.history[replace ? 'replaceState' : 'pushState']({}, '', url.pathname + url.search)
  listeners.forEach(fn => fn())
}

export function useLocation() {
  const read = () => ({ path: window.location.pathname.replace(/\/+$/, '') || '/',
                        query: new URLSearchParams(window.location.search) })
  const [loc, setLoc] = useState(read)
  useEffect(() => {
    const update = () => setLoc(read())
    listeners.add(update)
    window.addEventListener('popstate', update)
    return () => { listeners.delete(update); window.removeEventListener('popstate', update) }
  }, [])
  return loc
}

// For <a href onClick={link(href)}>: keeps cmd-click / middle-click opening a new tab.
export function link(href) {
  return e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(href)
  }
}
