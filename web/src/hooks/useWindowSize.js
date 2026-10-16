import { useState, useEffect } from 'react'

// The page is zoomed by the Appearance text size (--scale on <html>), so layout
// breakpoints use the width in page pixels: 1600px at 120% lays out like 1333px.
export function layoutWidth() {
  const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--scale')) || 1
  return window.innerWidth / scale
}

export function useWindowSize() {
  const measure = () => ({ w: layoutWidth(), h: window.innerHeight })
  const [size, setSize] = useState(measure)
  useEffect(() => {
    const h = () => setSize(measure())
    window.addEventListener('resize', h)
    window.addEventListener('senseless:scale', h)  // fired by applyTokens when --scale changes
    return () => { window.removeEventListener('resize', h); window.removeEventListener('senseless:scale', h) }
  }, [])
  return size
}
