import { useState, useEffect } from 'react'

export function useCountUp(target, duration = 1200) {
  const [val, setVal] = useState(0)
  useEffect(() => {
    const num = parseFloat(target)
    // Callers render their raw text when there's no number, so park at 0 rather than
    // NaN — a NaN here is what showed until the first animation frame.
    if (isNaN(num)) { setVal(0); return }
    const start = Date.now()
    const decimals = typeof target === 'string' && target.includes('.')
    const shown = x => (decimals ? x.toFixed(1) : Math.round(x))
    let frame
    const tick = () => {
      const progress = Math.min((Date.now() - start) / duration, 1)
      setVal(shown((1 - Math.pow(1 - progress, 3)) * num))
      if (progress < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    // Frames stop in a background tab; always land on the real value.
    const done = setTimeout(() => setVal(shown(num)), duration + 50)
    return () => { cancelAnimationFrame(frame); clearTimeout(done) }
  }, [target, duration])
  return val
}
