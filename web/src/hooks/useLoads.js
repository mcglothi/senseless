import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/api'

// Detected load patterns (/api/loads, recomputed server-side every minute) and the
// two things you can do to one: name it, or ignore it.
export function useLoads() {
  const [data, setData] = useState({ patterns: [], computed_at: 0 })
  const [error, setError] = useState(null)
  const load = useCallback(() => api('GET', '/api/loads').then(d => { setData(d); setError(null) }).catch(e => setError(e.message)), [])
  useEffect(() => {
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [load])
  const patch = (p, upd) => setData(d => ({ ...d, patterns: d.patterns.map(x => (x.id === p.id ? { ...x, ...upd } : x)) }))
  const name = useCallback(async (p, name) => {
    const r = await api('POST', `/api/loads/${p.id}/name`, { name })
    patch(p, { name: r.name, ignored: r.ignored })
  }, [])
  const ignore = useCallback(async (p, ignored = true) => {
    const r = await api('POST', `/api/loads/${p.id}/ignore`, { ignored })
    patch(p, { ignored: r.ignored })
  }, [])
  return { ...data, error, reload: load, name, ignore }
}

// Which pattern a single step most likely belongs to: same legs, size within 12% of the centre.
export function matchStep(patterns, { leg, w }, both = false) {
  const legs = both ? 'both' : `L${leg}`
  const a = Math.abs(w)
  let best = null, err = 0.12
  for (const p of patterns) {
    if (p.legs !== legs) continue
    const e = Math.abs(a - p.center_w) / p.center_w
    if (e <= err) { best = p; err = e }
  }
  return best
}
