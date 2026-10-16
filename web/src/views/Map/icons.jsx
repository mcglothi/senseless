// Line icons for map markers (24×24, stroke-only so they take any theme colour). Names match the
// server's icon allowlist (senseless/floorplans.py, ui/floorplan.js ICONS).
const RING = 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0'
const PATHS = {
  plug: 'M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0V8zM12 18v4',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7l1-8z',
  fan: `${RING}M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0M12 11V5.5M13 12.5l4.7 2.8M11 12.5l-4.7 2.8`,
  heater: 'M4 6h16v12H4zM8 6v12M12 6v12M16 6v12M6 18v2M18 18v2',
  ac: 'M3 5h18v8H3zM6 10h12M7 16c0 2 2 2 2 4M12 16v4M17 16c0 2-2 2-2 4',
  fridge: 'M6 2h12v20H6zM6 10h12M9 5v2M9 13v3',
  microwave: 'M2 5h20v14H2zM5 8h11v8H5zM19 8.5v1M19 12.5v1',
  oven: 'M4 3h16v18H4zM4 8h16M7 5.5h.01M10 5.5h.01M8 11h8v7H8z',
  washer: 'M4 2h16v20H4zM4 6h16M7 4h1M12 14m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0',
  dryer: 'M4 2h16v20H4zM4 6h16M7 4h1M12 14m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0M9.5 14c1-1.2 2-1.2 2.5 0s1.5 1.2 2.5 0',
  dishwasher: 'M4 2h16v20H4zM4 7h16M7 4.5h3M8 12h8v6H8z',
  pump: 'M9 3h6v4H9zM5 7h14v5a7 7 0 0 1-14 0zM12 11v4',
  water: 'M12 3c3 4.5 6 7.5 6 11a6 6 0 0 1-12 0c0-3.5 3-6.5 6-11z',
  light: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z',
  tv: 'M3 6h18v12H3zM8 21h8M12 18v3',
  computer: 'M3 4h18v12H3zM2 20h20',
  router: 'M3 13h18v6H3zM7 16h.01M11 16h.01M8 13 6 6M16 13l2-7',
  car: 'M3 13l2-6h14l2 6v5H3zM3 13h18M7 18v2M17 18v2M7 15.5h.01M17 15.5h.01',
  tub: 'M3 12h18v2a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5zM5 12V5a2 2 0 0 1 4 0M7 19l-1 2M17 19l1 2',
  tool: 'M14.5 5.5a4 4 0 0 0 4.9 4.9L10 19.8a2.1 2.1 0 0 1-3-3l9.4-9.4a4 4 0 0 0-1.9-1.9z',
  panel: 'M5 2h14v20H5zM9 6h2M13 6h2M9 10h2M13 10h2M9 14h2M13 14h2M9 18h6',
  other: `${RING}M12 16.5h.01M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5`,
}

export function Icon({ name, size = 18, color = 'currentColor', strokeWidth = 1.8, style }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0, display: 'block', ...style }}>
      <path d={PATHS[name] ?? PATHS.plug} />
    </svg>
  )
}
