// Senseless API client. Writes surface errors; the caller shows them.
export async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  })
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    const err = new Error(data?.error || res.statusText)
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

// Is the user in the middle of typing? Background refreshes must not re-render
// over an input — it destroys focus and text.
export function busyTyping() {
  const el = document.activeElement
  return !!el && el.matches?.('input, textarea, select') && el.type !== 'checkbox'
}
