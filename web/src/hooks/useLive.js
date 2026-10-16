import { useEffect, useRef, useState } from 'react'
import { api } from '../api/api'

// The live feed, shared by every view: the latest realtime update, a rolling
// per-leg series for charts, and recent step events.
//
// History comes from /api/series and /api/deltas once; after that /api/stream
// (server-sent events, ~2 Hz) keeps it current without polling. Updates fold into
// fixed buckets, so a chart re-renders a few hundred points, not thousands.
export const BUCKET_S = 5
export const WINDOW_S = 30 * 60
const MAX_DELTAS = 400

// Fold one reading into the bucket list (running average per bucket); drops buckets
// older than the window. Pure, so it's unit-tested.
export function foldReading(buckets, { t, w, l1, l2 }, bucketS = BUCKET_S, windowS = WINDOW_S) {
  const key = Math.floor(t / bucketS) * bucketS
  const last = buckets[buckets.length - 1]
  let next
  if (last && last.t === key) {
    const n = (last.n ?? 1) + 1
    const avg = (a, b) => (a == null ? b : b == null ? a : a + (b - a) / n)
    next = buckets.slice(0, -1).concat({ t: key, n, w: avg(last.w, w), l1: avg(last.l1, l1), l2: avg(last.l2, l2) })
  } else if (!last || key > last.t) {
    next = buckets.concat({ t: key, n: 1, w, l1, l2 })
  } else {
    return buckets // late reading for an old bucket: ignore
  }
  const cutoff = key - windowS
  let i = 0
  while (i < next.length && next[i].t < cutoff) i++
  return i ? next.slice(i) : next
}

export function useLive() {
  const [latest, setLatest] = useState(null)
  const [series, setSeries] = useState([])
  const [deltas, setDeltas] = useState([])
  const [status, setStatus] = useState({ state: 'connecting', healthy: false })
  const lastAt = useRef(0)

  useEffect(() => {
    let es, retry, closed = false
    const load = async () => {
      const [s, d] = await Promise.all([
        api('GET', `/api/series?minutes=${WINDOW_S / 60}&bucket=${BUCKET_S}`),
        api('GET', `/api/deltas?minutes=${WINDOW_S / 60}`),
      ])
      setSeries(s.map(r => ({ ...r, n: 1 })))
      setDeltas(d.map(x => ({ t: x.ts, leg: x.channel + 1, w: x.w, frame: x.start_frame })).slice(-MAX_DELTAS))
    }
    const connect = () => {
      es = new EventSource('/api/stream')
      es.addEventListener('snapshot', e => {
        const snap = JSON.parse(e.data)
        if (snap.latest) setLatest(snap.latest)
        // arm the stale watchdog from the snapshot too: if no update follows, "Live" must not stick
        if (snap.healthy) lastAt.current = Date.now() - (snap.last_message_age_seconds ?? 0) * 1000
        setStatus({ state: 'live', healthy: snap.healthy, monitorOnline: snap.monitor_online })
      })
      es.onmessage = e => {
        const ev = JSON.parse(e.data)
        if (ev.type === 'rt') {
          lastAt.current = Date.now()
          setLatest(ev)
          const [l1, l2] = ev.channels ?? []
          setSeries(b => foldReading(b, { t: ev.t, w: ev.w, l1, l2 }))
          if (ev.deltas?.length) {
            setDeltas(prev => prev.concat(ev.deltas.map(d => ({ t: ev.t, leg: d.channel + 1, w: d.w, frame: d.start_frame ?? d.frame })))
              .slice(-MAX_DELTAS))
          }
          setStatus(s => (s.state === 'live' && s.healthy ? s : { ...s, state: 'live', healthy: true }))
        } else if (ev.type === 'hello') {
          setStatus(s => ({ ...s, monitorOnline: ev.payload?.online }))
        }
      }
      es.onerror = () => {
        setStatus(s => ({ ...s, state: 'reconnecting', healthy: false }))
        es.close()
        if (!closed) retry = setTimeout(connect, 3000)
      }
    }
    load().catch(() => {}).finally(() => { if (!closed) connect() })

    // No update for 15 s while "connected" means the feed is stale, even if the socket is open
    const watch = setInterval(() => {
      if (lastAt.current && Date.now() - lastAt.current > 15000) setStatus(s => ({ ...s, healthy: false }))
    }, 5000)
    return () => { closed = true; clearTimeout(retry); clearInterval(watch); es?.close() }
  }, [])

  return { latest, series, deltas, status }
}

// Today's energy summary; refreshed every 30 s (it's an aggregate, not a live number).
export function useToday() {
  const [today, setToday] = useState(null)
  useEffect(() => {
    let alive = true
    const load = () => api('GET', '/api/today').then(d => { if (alive) setToday(d) }).catch(() => {})
    load()
    const id = setInterval(load, 30000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  return today
}
