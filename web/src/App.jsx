import { useEffect, useState } from 'react'
import { ThemeContext, C, DENSITY } from './ui/theme'
import { Toaster, Empty } from './ui/primitives'
import { TopBar } from './components/TopBar'
import { AppearanceDrawer } from './components/Appearance/AppearanceDrawer'
import { useAppearance } from './hooks/useAppearance'
import { useWindowSize } from './hooks/useWindowSize'
import { useLive, useToday } from './hooks/useLive'
import { busyTyping } from './api/api'
import { useLocation } from './router'
import { NowView } from './views/Now/NowView'
import { LoadsView } from './views/Loads/LoadsView'
import { TimelineView } from './views/Timeline/TimelineView'
import { HuntView } from './views/Hunt/HuntView'
import { HistoryView } from './views/History/HistoryView'
import { useLoads } from './hooks/useLoads'

export default function App() {
  const { w } = useWindowSize()
  const isMobile = w < 640
  const isTablet = w < 1024
  const appearance = useAppearance()
  const [appearanceOpen, setAppearanceOpen] = useState(false)
  const live = useLive()
  const today = useToday()
  const loads = useLoads()
  const { path } = useLocation()

  // Shift+L flips dark/light anywhere (not while typing)
  const { toggleMode, settings, prefersLight } = appearance
  useEffect(() => {
    const onKey = e => {
      if (e.key !== 'L' || !e.shiftKey || e.metaKey || e.ctrlKey || e.altKey || busyTyping()) return
      e.preventDefault()
      toggleMode()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggleMode])

  const light = settings.mode === 'light' || (settings.mode === 'system' && prefersLight)
  const sp = n => Math.round(n * (DENSITY[settings.density] ?? 1))
  const theme = { accent: C.accent, cardRadius: settings.radius, isMobile, isTablet, sp }

  return (
    <ThemeContext.Provider value={theme}>
      <TopBar status={live.status} frame={live.latest?.frame} isMobile={isMobile} light={light}
        onOpenAppearance={() => setAppearanceOpen(true)} onToggleMode={toggleMode} />
      <AppearanceDrawer open={appearanceOpen} onClose={() => setAppearanceOpen(false)} appearance={appearance} />
      <main style={{ width: '100%', maxWidth: 1440, margin: '0 auto', padding: isMobile ? sp(14) : `${sp(22)}px ${sp(28)}px ${sp(40)}px` }}>
        {path === '/' ? <NowView live={live} today={today} loads={loads} /> : path === '/loads' ? <LoadsView loads={loads} /> : path === '/timeline' ? <TimelineView loads={loads} /> : path === '/hunt' ? <HuntView live={live} /> : path === '/history' ? <HistoryView /> : (
          <Empty title="Coming soon">
            History is on the roadmap.
          </Empty>
        )}
      </main>
      <Toaster />
    </ThemeContext.Provider>
  )
}
