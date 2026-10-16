import { useState, useEffect } from 'react'

export function useEntrance(delay = 0) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const id = setTimeout(() => setVisible(true), delay)
    return () => clearTimeout(id)
  }, [])
  return visible
}
