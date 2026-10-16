# Changelog

All notable changes, newest first. Versions are git tags; images are `ghcr.io/mcglothi/senseless:<version>`.

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
