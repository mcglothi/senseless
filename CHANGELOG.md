# Changelog

All notable changes, newest first. Versions are git tags; images are `ghcr.io/mcglothi/senseless:<version>`.

## 0.13.0 (2026-10-16)

### Changed
- **The bill runs on data you own.** The cycle's daily totals now come from Senseless's own recorded readings
  whenever a day is (nearly) fully recorded, then from imported utility-meter data, and only then from Sense's
  cloud history (days before Senseless was installed, or gaps). Time-of-use shares and recent hourly profiles
  follow the same order. If Sense's servers are unreachable or gone, the Bill page keeps working; each day in
  `/api/billing/cycle` reports its `source`, with a `day_sources` summary.

## 0.12.1 (2026-10-16)

### Fixed
- Shared switch-offs only pair loads of at least 100 W each. Small fragments (a washer's agitation steps) were being
  closed together by coincidence and cluttered the run list: 23 shared closures on a 12-hour replay drop to 4
  plausible ones, with motors and the washer cycle unchanged.

## 0.12.0 (2026-10-16)

### Added
- **Motor loads get real runs.** Pumps, compressors and fans draw a start-up surge, so their switch-on looked bigger
  than their switch-off and the two never paired (no runtime, no energy). A run is now sized by the power the leg
  settles at a few seconds after the switch, with the surge kept as `inrush_w`; patterns group by running level, and
  existing names follow their appliance. Replayed on real data: a pool pump that showed as an unmatched 2.2 kW
  start is one 08:31-14:28 run at 0.9 kW (5.4 kWh); a well pump goes from an unmatched 1.7 kW start to a closed
  0.8 kW run.
- **Shared switch-offs**: when a timer or power strip turns several loads off at once, one switch-off can close
  two runs that started within 10 minutes of each other (`closed_by: "shared-off"`).

### Changed
- "Was it still running?" checks are now relative to the leg's level just before a run started, with slack that
  scales with the load, so a later unrelated switch-off can't close an old run while other loads keep the leg busy.

## 0.11.2 (2026-10-16)

### Fixed
- Loads: a named cycle now moves to the Named column (marked "cycle", with rename) like a named load, instead of
  staying in the Cycles section where saving its name looked like nothing happened. Cycles lists only unnamed
  programs; clearing a cycle's name sends it back there.

## 0.11.1 (2026-10-16)

### Fixed
- Cycles: a real wash pauses about four minutes to drain or fill before the spin, which split it into two
  cycles. Activity gaps up to 6 minutes now stay one cycle (replayed: the 2026-10-16 wash is one 08:19-09:06
  cycle again; 10 minutes would swallow unrelated later steps).

## 0.11.0 (2026-10-16)

### Added
- **Cycles**: appliances that run a program (washers, dishwashers) are detected as one event instead of dozens
  of fragments. A burst of steps on the same legs, confirmed by the leg's power staying up, becomes a cycle
  with its duration, energy and a fingerprint; similar cycles share a pattern you name on the Loads page
  (with suggestions). Fragment patterns leave the inbox. API: `/api/cycles`, `/api/cycle-patterns`
  (+ `/name`, `/ignore`); `/api/loads` patterns gain `part_of_cycles`, `/api/runs` rows gain `cycle`.

## 0.10.0 (2026-10-16)

### Added
- **Import your utility data** on the Bill page: drop bill PDFs and usage exports (several at once). Recognised
  by content: Green Button XML, Green Button CSV, Smart Meter Texas CSV, Avangrid/CMP hourly CSV, and CMP bill
  PDFs. Any other usage CSV can be taught once with a column mapping (matched later by a fingerprint of its
  header, so no column labels are stored). Names, addresses, account/ESIID/meter numbers are dropped on import.
  (Smart Meter Texas and Green Button CSV follow the published formats and await a real export to verify.)
- **Multi-utility pricing**: tariffs are data ("tariff packs"), and one engine prices flat, tiered and
  time-of-use rates, monthly or daily fixed charges with included kWh, usage credits (Texas-style), minimum
  bills and sales tax with an exempt amount. Pick utility, delivery plan and supply on the Bill page; add another
  utility's pack via `/api/billing/tariffs` (see notes/energy-providers.md). Central Maine Power ships built in.
- **Insights from your meter and bills**: the last 12 months priced on every plan of your utility (each month
  with its real hours, so time-of-use reflects winter evenings), usage vs outdoor temperature, your bill
  history checked against the model from each bill's own rates, and Sense compared with the utility meter.

### Changed
- With a year of meter data, plan comparisons use it instead of the last 7 days (a quiet week had made
  time-of-use look cheaper than it is), and the cycle projection follows last year's season.
- The Electric Technology rate is verified against CMP's tariff sheet (page 48.00) and shows its January 2027 end.

## 0.9.0 (2026-10-16)

### Added
- **Property map**: buildings, each with its own floors. `/map` shows the whole property: buildings on an
  uploaded site plan (draw, move and resize footprints) or as live tiles, each with live watts and today's
  energy. Click a building to zoom into it (`/map/b/{id}`): breadcrumb and building switcher, floors stacked
  by level (basement to attic) with live load per level, add floors above or below. The list view is a
  property-wide outline (building > floor > room > device). Building icons for house, garage, barn, shed,
  greenhouse, pool house, workshop and office.
- API: `/api/property` (nested view, site image upload), `/api/buildings` CRUD, floors gain `building_id`
  and `level`.

### Changed
- Existing floors move into a "Main house" building automatically (levels from their order); nothing is lost.
- Limits: up to 50 buildings and 60 floors per property (was 20 floors).

## 0.8.1 (2026-10-16)

### Fixed
- Bill: sales tax now matches a real CMP bill to the cent. Tax is 5.5% of the kWh above the 750 kWh
  exemption at their per-kWh delivery and supply rates; the fixed charge isn't taxed (it was taxed pro rata).
- Bill: the cycle start day accepts 29–31 (meter reads late in the month), clamped to short months.

## 0.8.0 (2026-10-16)

### Added
- **Bill** (`/bill`): this billing cycle priced by your tariff (fixed charge, delivery, supply, sales tax),
  projected to the cycle end from Sense's daily history, with what each extra kWh costs. The same usage is
  priced on every rate plan (CMP Rate A, time-of-use with your measured on-peak share, Electric Technology),
  so you can see if switching would save money. Enter real bills to check the estimate; optionally scale
  projections to match them. Settings for plan, supply price, cycle start day, tax rate and tax-free kWh.
- Built-in tariffs: CMP residential delivery rates effective 2026-07-01 and the 2026 Maine Standard Offer,
  with sources. A custom plan covers other utilities.
- Billing API: `/api/billing`, `/api/billing/cycle`, `/api/billing/estimate`, `/api/billing/bills`.

### Changed
- Now's "cost so far" and the always-on $/day and $/year use your tariff (marginal rate plus the fixed
  charge's daily share) instead of Sense's flat rate, which left out delivery.
- Phone nav: tighter tabs so all seven fit at 420 px; on narrower screens the current tab scrolls into view.

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
