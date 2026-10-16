# Changelog

All notable changes, newest first. Versions are git tags; images are `ghcr.io/mcglothi/senseless:<version>`.

## 0.7.1 (2026-10-16)

### Fixed
- Detection: small loads no longer show runs lasting hours. An off step used to close a much older on of
  similar size after the real off was missed, so one "run" swallowed many real cycles (a 56 W relay that
  runs for seconds showed a 4-hour run). Pairing now checks sustained leg power (the leg must actually stay
  up by the load's size), and runs far outside their own pattern's durations are marked unresolved. On a
  6 h production sample, runs over 30 min fell from 29 to 19 and the 5 h false run is gone. Readings are
  binned in SQL for this, so the per-minute recompute stays light.

## 0.7.0 (2026-10-16)

### Added
- **Device Hunt**: a Devices mode next to Breaker Hunt. Switch a device on and off; Senseless measures each
  cycle (legs, size, multi-stage starts, on-time), checks the cycles agree, suggests names (existing names,
  Sense's own guess, size-based ideas) and names the matching load pattern, creating it if the detector
  hasn't seen that load yet. Optionally pins the device in a Map room. Frequent small background loads are
  ignored by default.
- **Hunt API** (`/api/hunt`, `start`, `save`, `label`, `retry`, `stop`): the hunt now runs server-side, so
  a voice assistant can drive it. Each state carries a speakable `prompt` and the valid `actions`;
  `?wait=<version>` long-polls for the next change, and changes stream as `hunt` events on `/api/stream`.
- `GET /api`: a catalog of every endpoint, so agents can find their way around.

### Changed
- Breaker Hunt's measuring moved from the browser to the server (same math; the app is now a client of the
  shared session).

## 0.6.1 (2026-10-16)

### Fixed
- Phone nav: with the Map tab added, History wrapped onto a second row at phone widths. Tabs now sit on
  one row (tighter spacing) and scroll sideways on screens too narrow for that.

### Docs
- README screenshots refreshed: the Map tab now appears in the nav, and the stray `">` is gone.

## 0.6.0 (2026-10-16)

### Added
- **Map** (`/map`): upload a floor plan per floor, place named loads and live Sense devices on it, draw
  rooms. Devices show calm live states (off, on with watts, ≥ 1 kW aura); rooms and floors roll up live
  watts and today's energy. Edit mode with drag-and-drop or tap-to-place palette, marker drag and
  keyboard nudge, room draw/move/resize; inspector panel (bottom sheet on phones); list view; plan style
  (original/dimmed/inverted). Placements are relative to the image, so replacing it keeps them.
- Floor plan API: `/api/floors`, rooms, markers, and raw-body image upload (PNG/JPEG/WebP, magic bytes
  checked, 15 MB, 12000 px, custom-header + Origin check).

### Changed
- Load detection: linear-time run pairing; Sense hint assignment uses each turn-on once, nearest run first.

### Fixed
- A stray `">` no longer renders at the top of every page.
- Dark Reader no longer re-colours the app on top of its own themes.

## 0.5.0 (2026-10-16)

### Added
- **Breaker Hunt** (`/hunt`): phone-first flow for the panel. Capture a baseline, flip a breaker, see exactly
  what dropped on which leg (240 V breakers flagged), save the breaker map, track how much of the always-on load
  is explained.
- **History** (`/history`): days, months and years from Sense's cloud history: month calendar heatmap with
  drill-down, hourly and monthly bars, cost, average per day, fair comparison with the previous period, and
  Sense's per-device breakdown. Cached locally.
- **Sense naming hints**: when a pattern's runs line up with Sense's own detection turning a device on, the
  inbox and timeline offer "Sense: <device>" as a one-tap name.
- Loads: names already used on the same legs are suggested first; patterns sharing a name are grouped.
- Prometheus metrics for named loads: `senseless_load_running`, `senseless_load_energy_today_wh`,
  `senseless_patterns`, `senseless_detection_age_seconds`.
- Installable app (web manifest, icons, shortcuts to Breaker Hunt and Loads).
- Docs: `notes/detection.md` (how detection works) and `notes/senseless-api.md` (the HTTP API).

### Fixed
- Write endpoints return 400 instead of 500 for non-object JSON bodies.

## 0.4.0 (2026-10-15)

### Added
- **Timeline** (`/timeline`): per-leg power, step events and detected runs across any window; zoom chips,
  drag-to-zoom, pan, live follow, hover readout, run inspector with inline naming. Runs are keyboard accessible.
- `start`/`end` windows on `/api/series`, `/api/deltas`, `/api/runs`; series keeps per-bucket peaks.

### Fixed
- Times formatted with a 23-hour clock everywhere (midnight showed as "24:00" on some platforms).
- Hour ticks stay on local multiples across DST changes.

## 0.3.0 (2026-10-15)

### Added
- **Load detection** (`senseless/detect.py`): 240 V steps, multi-stage turn-ons, on/off run pairing, persistent
  patterns with user names. Recomputed every minute over 48 h.
- **Loads** (`/loads`): inbox of repeating unnamed patterns with fingerprints and name ideas, named loads,
  on-now strip; keyboard flow (J/K, Enter, X).

### Fixed
- A missed off can no longer be closed by an unrelated drop hours later.
- Runs before a monitor reboot are kept; cross-midnight loads count today's share of energy.

## 0.2.0 (2026-10-15)

### Added
- **Web UI** with the live **Now** view: per-leg power, today's energy and cost, the last 30 minutes with every
  on-board step, 240 V and repeating-load spotting, always-on estimate. Live over server-sent events.
- **Theming** (shared with Docket): base16 palettes, 570-scheme gallery, light/dark/system, accent, background,
  density. Default theme: Senseless.
- `/api/stream`, `/api/today`, `/api/series`, appearance endpoints.

### Fixed
- SIGTERM no longer hangs while a browser holds the event stream open.
- Settings saves no longer fail with "database is locked".
- Small labels meet contrast targets in every theme whose own text color allows it.

## 0.1.0 (2026-10-15)

### Added
- Collector: Sense realtime feed to SQLite plus raw archive, unattended-run hardening, `/metrics`, `/healthz`,
  Docker image.
