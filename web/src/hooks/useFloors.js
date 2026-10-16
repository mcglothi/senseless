import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/api'
import { toast } from '../ui/primitives'

export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
export const MAX_IMAGE_BYTES = 15 * 1024 * 1024

// Raw-body upload (not multipart). The custom header forces a CORS preflight, so no other site can post here.
async function putImage(id, file) {
  if (!IMAGE_TYPES.includes(file.type)) throw new Error('Use a PNG, JPEG or WebP image')
  if (file.size > MAX_IMAGE_BYTES) throw new Error('Image is over 15 MB')
  const res = await fetch(`/api/floors/${id}/image`, {
    method: 'PUT', body: file, headers: { 'Content-Type': file.type, 'X-Senseless-Upload': '1' },
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.error || res.statusText)
  return data
}

// Floors with their rooms and markers (/api/floors). Every write reloads the list; marker moves
// update locally first so a drag never snaps back while the request is in flight.
export function useFloors() {
  const [floors, setFloors] = useState(null)
  const [error, setError] = useState(null)
  // A reload only lands if it is the newest one and no write is still in flight; otherwise an older
  // GET could arrive late and put a just-dragged marker back where it was.
  const seq = useRef(0), pending = useRef(0)
  const load = useCallback(async () => {
    const mine = ++seq.current
    try {
      const f = await api('GET', '/api/floors')
      if (mine === seq.current && pending.current === 0) { setFloors(f); setError(null) }
    } catch (e) {
      if (mine === seq.current) setError(e.message)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const run = useCallback(async fn => {
    pending.current++
    try {
      return await fn()
    } catch (e) {
      toast(e.message)
      throw e
    } finally {
      pending.current--
      if (pending.current === 0) await load()   // callers rely on the list being fresh when this resolves
    }
  }, [load])

  const local = (kind, id, upd) => setFloors(fs => fs?.map(f => ({ ...f, [kind]: f[kind].map(x => (x.id === id ? { ...x, ...upd } : x)) })))

  return {
    floors, error, reload: load,
    addFloor: name => run(() => api('POST', '/api/floors', { name })),
    patchFloor: (id, b) => run(() => api('PATCH', `/api/floors/${id}`, b)),
    deleteFloor: id => run(() => api('DELETE', `/api/floors/${id}`)),
    uploadImage: (id, file) => run(() => putImage(id, file)),
    addRoom: (floorId, b) => run(() => api('POST', `/api/floors/${floorId}/rooms`, b)),
    patchRoom: (id, b) => { local('rooms', id, b); return run(() => api('PATCH', `/api/rooms/${id}`, b)) },
    deleteRoom: id => run(() => api('DELETE', `/api/rooms/${id}`)),
    addMarker: (floorId, b) => run(() => api('POST', `/api/floors/${floorId}/markers`, b)),
    patchMarker: (id, b) => { local('markers', id, b); return run(() => api('PATCH', `/api/markers/${id}`, b)) },
    deleteMarker: id => run(() => api('DELETE', `/api/markers/${id}`)),
  }
}
