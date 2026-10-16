# Senseless's own HTTP API

These routes belong to the local Senseless server, not the [Sense cloud API](api.md).
This inventory is verified from [`senseless/web.py`](../senseless/web.py) and its
helpers. It is a code reference, not a report of live HTTP tests.

The server binds to `SENSELESS_HTTP_HOST` (default `0.0.0.0`) and
`SENSELESS_HTTP_PORT` (default `8780`). Responses are JSON unless stated otherwise.
There is no authentication in `make_app`. The write checks below are not login.

## Time windows

`/api/deltas`, `/api/series`, `/api/runs` and `/api/cycles` accept Unix seconds `start` and `end`
for a half-open interval `[start, end)`. If either is supplied, `minutes` is ignored.
Missing `end` means now; missing `start` means the route's default span before end.
Otherwise, `minutes` selects a span ending now. A span over the route's cap moves
start forward. Non-numeric values or `end <= start` return 400.

| Route | Default span | Maximum span |
|---|---|---|
| `/api/deltas` | 10 minutes | 24 hours |
| `/api/series` | 30 minutes | 7 days |
| `/api/runs` | 180 minutes | 48 hours |
| `/api/cycles` | 180 minutes | 48 hours |

`/api/readings` accepts only `minutes` (default 10, capped at 360). It selects
`ts > now - minutes * 60`. Its helper checks numeric conversion and the upper cap,
but does not enforce positive minutes.

## Live data and history routes

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/healthz` | None | `{healthy, age}`. HTTP 200 when healthy, 503 otherwise. Age is seconds since the last realtime update, or null. |
| GET | `/metrics` | None | Prometheus text: feed health, power per leg, counters, database size; detection metrics when available: `senseless_load_running{load,legs}`, `senseless_load_energy_today_wh{load,legs}`, `senseless_patterns{state}`, `senseless_detection_age_seconds`, `senseless_cycles_today{name}`. |
| GET | `/api/status` | None | `healthy`, `connected`, `monitor_online`, `last_message_age_seconds`, `uptime_seconds`, `reconnects`, `latest` feed payload. |
| GET | `/api/deltas` | Time window | Array of `ts`, `start_frame`, `frame`, `channel`, signed `w`; ordered by start frame. |
| GET | `/api/readings` | `minutes` | Array of stored reading rows, ordered by frame. Fields: `generation`, `frame`, `ts`, `recv_ts`, `w`, `l1_w`, `l2_w`, `l1_v`, `l2_v`, `hz`, `grid_w`, `solar_w`. |
| GET | `/api/today` | None | Since local midnight: `since`, `kwh`, `cost`, `cost_cents_per_kwh`, `peak_w`, `avg_w`, `coverage`, and 24 `hourly_w` values. Cost uses the latest feed's `defaultCost`; missing data can yield null values. |
| GET | `/api/series` | Time window; `bucket` seconds, default 10 | Array of `{t, w, l1, l2, w_max}` power buckets. Bucket is clamped to 1–3600 s, then enlarged if needed to keep the span to at most 5000 buckets. Invalid numeric bucket returns 400. Empty buckets are omitted. |
| GET | `/api/stream` | None | SSE (`text/event-stream`). First a named `snapshot` event with status fields and `latest` (without uptime or reconnect count). Then JSON feed events as they arrive. A keepalive comment is sent after 15 s without an event. |
| GET | `/api/history` | `scale=day\|month\|year`, default `month`; required `date=YYYY-MM-DD` | Cached Sense cloud history: `scale`, `start`, `period`, `kwh_total`, `cost_total_cents`, bucket arrays `kwh` and `cost_cents`, `devices`, `cached`, and `stale: true` on stale fallback. Device fields: `id`, `name`, `icon`, `kwh`, `cost_cents`, `pct`. |

Today's local timezone is `SENSELESS_TZ`, then `TZ`, then `America/New_York`.
Today energy integrates readings and excludes gaps longer than 10 s. History
validation errors about date, scale or future periods return 400; other
`HistoryError`s return 502. A cached copy can be returned if refresh fails.
History routes return 404 if history is not configured.

## Detection routes

See [load detection](detection.md) for pairing, status and energy semantics.
These routes return 404 when detection is not running.

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/loads` | None | `{computed_at, patterns}`. Patterns include saved fields and current-window/today statistics; sorted by today's Wh, then window run count, descending. |
| GET | `/api/runs` | Time window | Array of overlapping runs. For unresolved runs, only the start counts toward inclusion. |
| POST | `/api/loads/{id}/name` | Integer pattern ID; JSON `{ "name": "Microwave" }` | Updated stored pattern row. Null, blank or omitted name clears it. Names must be strings or null; trimmed to 60 characters. Clears ignored state. |
| POST | `/api/loads/{id}/ignore` | Integer pattern ID; JSON `{ "ignored": true }` | Updated stored pattern row. Omitted `ignored` defaults to true; supplied values use Python truth conversion. |
| GET | `/api/cycles` | Time window | Array of overlapping cycle envelopes with pattern ID and current name. |
| GET | `/api/cycle-patterns` | None | `{computed_at, patterns}`: saved cycle pattern centres plus today's/48-hour statistics. |
| POST | `/api/cycle-patterns/{id}/name` | Integer cycle pattern ID; JSON `{ "name": "Washer" }` | Updated stored row; string or null, trimmed to 60 characters. Null, blank or omitted clears name; naming clears ignored state. |
| POST | `/api/cycle-patterns/{id}/ignore` | Integer cycle pattern ID; JSON `{ "ignored": true }` | Updated stored row. Boolean required; omitted defaults to true. |

Stored pattern fields are `id`, `legs`, `stages`, `center_w`, `n`, `name`, `ignored`,
`created`, `named_at`, `last_seen`, `inrush_w`. Pattern `inrush_w` is the median
reported startup size among surge-adjusted runs in the current window, or null
when none qualify. `center_w` clusters their settled running sizes.
Load responses also include `runs_window`,
`runs_today`, `wh_today`, `wh_window`, `last_ts`, `running`, `example`,
`median_duration_s`, `closed_runs`, `sense_hint`, `cycle_runs`, `part_of_cycles`.
`part_of_cycles` is true when at least 70% of the pattern's window runs are cycle
members. All rows remain available and nameable; an inbox client can hide flagged
fragments and offer an expanded view. The frontend has not yet adopted this flag.
Run fields are `generation`, `start_frame`, `start_ts`, `end_frame`, `end_ts`,
`legs`, `w`, `inrush_w`, `off_w`, `stages`, `duration_s`, `running`, `status`, `wh`, `pattern`,
`cycle` (cycle ID string or null), `closed_by`.
Run `closed_by` is `"shared-off"` when two runs switched off together at one
observed off; otherwise null. Both runs retain that off's full `off_w`; do not
sum those values as separate measured offs. Each run's `wh` uses its own size
and duration.
Run `inrush_w` is the monitor's reported on-size when covered pre/settled readings
identify a startup surge; otherwise null. `w`, pairing and `wh` then use the
settled rise. `stages` retain the original reported watts. See
[settled sizing rules and limits](detection.md#steps-to-runs).
Invalid pattern IDs return 400; unknown IDs return 404.

Cycle fields: `id` (`generation:legs:start_frame`, stable while the burst start
stays in the window), `generation`, `start_frame`, `start_ts`, `end_ts`, `duration_s`,
`legs`, `steps`, `step_rate` (per minute), `median_step_w`, `p90_step_w`, `energy_wh`,
`peak_w`, `has_heat`, `member_run_keys` (arrays of generation, start frame, legs),
`pattern` (cycle pattern ID), `name`. Energy and peak are above the two-minute
pre-burst leg baseline. End is the last candidate on-step, not a verified off.

Stored cycle pattern fields: `id`, `legs`, `duration_s`, `energy_wh`, `step_rate`,
`median_step_w`, `n`, `name`, `ignored`, `created`, `named_at`, `last_seen`.
Statistics add `cycles_today`, `cycles_window`, `kwh_today`, `kwh_window`,
`median_duration_s`. Counts use cycle start; today's energy apportions total
cycle energy by duration after local midnight. `n` is the current window count;
`last_seen` is the most recent cycle start. Named patterns remain listed with
zero counts when absent. The metric `senseless_cycles_today{name}` sums start
counts of all cycle patterns sharing a name, including ignored named patterns.
See [cycles.md](cycles.md) for detection rules and energy limitations.

## Appearance and breaker map

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/themes` | None | `{themes}`: bundled and installed themes. |
| GET | `/api/themes/gallery` | `refresh=1` requests refresh, subject to refresh throttling | Gallery object with `themes`, normally `fetched_at`, and an `error` on fetch failure. Can serve cached data. |
| POST | `/api/themes/install` | JSON `{ "id": "theme-id" }` | `{themes}` after installation. |
| DELETE | `/api/themes/{id}` | Theme ID in path; no body | `{themes}` after removal. |
| GET | `/api/appearance` | None | Current appearance settings including defaults. |
| POST | `/api/appearance` | JSON settings patch (fields below) | Merged, validated, saved settings. |
| GET | `/api/circuits` | None | Circuit rows ordered by creation time. |
| POST | `/api/circuits` | JSON `breaker`, optional `name`, numeric `l1_w` and `l2_w` | New circuit row: `id`, `breaker`, `name`, `l1_w`, `l2_w`, `created`. |
| DELETE | `/api/circuits/{id}` | Integer circuit ID; no body | `{ok: true}`; invalid ID returns 400, unknown ID returns 404. |

Appearance patch fields:

- `mode`: `dark`, `light`, `system`; `dark` and `light`: known theme IDs.
- `accent`: null, a base16 slot `base08`–`base0F`, or `#rrggbb`.
- `radius`: 0–24; `textureStrength` and `glowStrength`: 0–30; `textScale`: 80–140.
- `texture`: `none`, `dots`, `grid`, `crosshair`, `scanlines`, `hatch`.
- `glow`: `none`, `corners`, `vignette`, `aurora`.
- `density`: `compact`, `comfortable`, `spacious`; `motion`: `system`, `full`, `reduced`.

Unknown appearance fields are rejected. Circuit `breaker` must be a nonblank
string of at most 20 characters; `name` is null or a string of at most 60.
Leg watts must be numbers from −20000 to 20000, excluding booleans; saved values
are rounded to one decimal. Appearance and circuit validation errors return
400 with `{error}`. Their routes return 404 when the corresponding service is
not configured. These settings, circuits and patterns live in `settings.db`.

## Hunt sessions (breaker and device)

One shared hunt session for the house lives on the server, so the app and any API client (a script,
a voice assistant walking the house with you) drive and see the same thing. Every change is also pushed
on `/api/stream` as `{"type": "hunt", "hunt": <view>}`. `GET /api` lists every endpoint with a one-line
description, for agents discovering the API.

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/hunt` | optional `wait=<version>` long-polls until the session changes; `timeout` seconds (default 25, max 55) | The session view (below). On timeout, the unchanged view. |
| POST | `/api/hunt/start` | JSON `{"mode": "breaker"\|"device", "include_background": false}` | The view. Restarts any running hunt. In breaker mode, also "next breaker" after a save. |
| POST | `/api/hunt/save` | JSON `{"breaker": "14", "name": "Basement"}` | `{saved, hunt}`: breaker mode, stores the measured drop (same row as `POST /api/circuits`). |
| POST | `/api/hunt/label` | JSON `{"name": "Microwave", "room_id": 7}` (`room_id` optional) | `{pattern, measurement, hunt}` and, with `room_id`, `marker`: device mode, names the measured load's pattern (created if new) and optionally pins it in a Map room. |
| POST | `/api/hunt/retry` | none (empty body is fine) | Breaker: re-baseline. Device: discard the current measurement and wait for a fresh on. |
| POST | `/api/hunt/stop` | none | The idle view. |

Commands that don't fit the current phase return 409 with `{error, hunt}`. Empty-body POSTs are accepted,
still subject to the same-origin rule below.

Session view fields: `version`, `epoch` (server start; with `version`, orders views), `mode`, `phase`,
`live`, `prompt` (one or two short sentences meant to be read aloud), `actions` (commands valid now),
`note`, `last` (what the previous save/label stored), `baseline`, `noise_steps`. Breaker mode adds `drop`
`{l1, l2, total, steps, both, meaningful}`. Device mode adds `on` (the switch-on being measured),
`measurement` `{w, legs, stages, cycles, on_sizes, off_sizes, durations_s, consistent, sense_guess,
ideas, matches}`, `capturing`, `ignoring` (background patterns skipped) and `ignored_steps`.

Phases:

- breaker: `idle` → `settling` (4 s) → `watching` (switch a breaker off; `drop` updates) → `save` →
  `flipback` (switch it back on) → `start` again.
- device: `idle` → `settling` → `ready` (waiting for an on step) → `on` (measured the on; waiting for the
  matching off) → `measured` (a full cycle; switch again to confirm, or `label`). `label` returns to
  `ready` for the next device. Only on-steps of at least 30 W count (the detector's floor). By default,
  small loads that cycle all day (≥ 40 runs in 48 h, median on-time ≤ 10 min, ≤ 300 W) are ignored, so a
  busy relay doesn't become "the device"; pass `include_background: true` to hunt one of those down.

Voice assistant loop (illustrative):

```text
POST /api/hunt/start {"mode":"device"}           → say prompt
loop: GET /api/hunt?wait=<version>                → when prompt changes, say it
user: "it's the microwave, in the kitchen"        → POST /api/hunt/label {"name":"Microwave","room_id":<kitchen>}
user: "that wasn't me" / "measure again"          → POST /api/hunt/retry
user: "done"                                      → POST /api/hunt/stop
```

Room ids come from `GET /api/floors` (each floor's `rooms`).

## Billing

Tariff-based bill estimates. Settings, usage, mappings, user tariffs and bills live in `settings.db`.
Settings now include `pack/plan/supply`; CMP defaults are `us-me-cmp/rate-a/standard-offer-2026`.
Legacy plan IDs are accepted and migrated to canonical IDs. Existing numeric supply/tax/custom
settings remain an editing compatibility view for CMP; other packs carry those charges in data.
`plans` includes selected-pack plans (plus legacy aliases for CMP). `supply` is null for bundled plans.
See [provider implementation notes](energy-providers.md).

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/billing` | None | `{settings, rates, plans, supply}`. `rates` = `{marginal_cents_per_kwh, fixed_per_day, plan, plan_label}`; `plans` carry `fixed`, `fixed_kwh`, `delivery` or `tou`, `source`, `effective`. |
| POST | `/api/billing` | JSON patch of `pack`, `plan`, `supply` (ID or null for bundled plans; legacy `cmp-a`, `cmp-a-tou`, `cmp-et`, `custom` accepted), `supply_per_kwh`, `supplier`, `billing_day` (1–31; clamped to short months), `tax_rate`, `tax_exempt_kwh`, `custom` (`fixed`, `fixed_kwh`, `delivery`), `calibrate` | Saved settings. Unknown fields and out-of-range values return 400. |
| GET | `/api/billing/cycle` | None | The cycle containing today: `start`, `end`, `days`, `elapsed_days`, `daily` kWh, `kwh_so_far`, `kwh_per_day` (last 7 complete days), `kwh_projected`, `so_far` and `projected` bills, `compare` (each plan's projected total and delta), `on_peak_share` (weekday 5–9 pm share over the last 7 complete days), `calibration`, `sense_cost_so_far`. 502 when Sense history is unavailable. |
| PUT | `/api/billing/import` | Raw interval usage XML/CSV or CMP bill PDF/text; `X-Senseless-Upload: 1` | Usage: `{kind: "meter-csv", added, updated, from, to, importer, label}` (`from`/`to` are first/last Unix interval starts). Bill: `{kind: "bill-pdf", ...stored_bill, importer, label}`. Content detection ignores filenames and MIME types. See upload rules below. |
| POST | `/api/billing/import/preview` | Same raw body, origin, header and size guards as import | `{importer, label, confidence, kind, sample, header}`. First five normalized usage rows (or one normalized bill); no writes. Unknown CSVs return null importer/kind, empty sample and a redacted header. |
| GET | `/api/billing/csv-profiles` | None | Saved mappings as a JSON array. |
| POST | `/api/billing/csv-profiles` | JSON `{name, match_headers, columns, time_format?, timezone?, units}` | Validated mapping; add/replace by `name` (a 1–64 character alphanumeric/underscore/hyphen slug). Identity columns cannot be mapped. |
| DELETE | `/api/billing/csv-profiles?name=` | Mapping name | `{ok: true}`; 404 if missing. Same-origin guard. |
| GET | `/api/billing/tariffs` | None | Array of `{id, utility, region, plans, supply}` summaries (IDs, labels, kind, dates, sources). |
| GET | `/api/billing/tariffs/{pack}` | Pack ID | Complete data-only pack; 404 if missing. |
| POST | `/api/billing/tariffs` | Complete JSON tariff pack | Validated pack; add/replace user IDs. Built-in IDs are read-only (400). Replacements must preserve the selected plan/supply. Same JSON/origin checks as other writes. |
| DELETE | `/api/billing/tariffs/{pack}` | Pack ID | `{ok: true}`; 404 if missing, 400 for built-in or selected user pack. Same-origin guard. |
| GET | `/api/billing/insights` | None | `{meter, sense_vs_meter, plans_12mo, weather, bills}`; unavailable analyses are null. 502 when configured Sense history is unavailable. |
| GET | `/api/billing/estimate` | `kwh`, optional `on_peak_kwh` (for a single TOU period; multiple periods require an hourly profile through the Python engine/cycle analysis), `plan`, `months` | One bill: `fixed`, `delivery`, `supply`, `tax`, `total`, `effective_cents_per_kwh`, `marginal_cents_per_kwh`, normalized charge `lines`. |
| GET | `/api/billing/bills` | None | Entered bills, newest first, each with `estimate`, `diff` and `ratio`. |
| POST | `/api/billing/bills` | JSON `start`, `end` (YYYY-MM-DD, 7–62 days apart), `kwh`, `amount` | The new bill with its comparison. |
| DELETE | `/api/billing/bills/{id}` | None | `{ok: true}`; 404 if unknown. |

Bill model: `fixed` (per month; Rate A's covers the first 50 kWh of delivery) + delivery per kWh
(time-of-use: on-peak/off-peak) + supply per kWh + sales tax on the kWh above the exempt amount at
their per-kWh delivery and supply rates (Maine: first 750 kWh/month exempt; the fixed charge is not
taxed, as on a real CMP bill). The marginal rate is the difference between the bill for N+1 and N kWh. Daily kWh comes from Sense's cloud history (so a cycle that
started before Senseless was installed is complete); `/api/today`'s `cost` uses the marginal rate
plus the day's share of the fixed charge.

Billing imports have the same origin check as image uploads and require the custom upload header.
The limit is **20 MiB**, enforced on both Content-Length and streamed bodies. Invalid/unsupported files
return 400 with private errors (unknown CSV errors include only a redacted header). Detection uses
content: Green Button ESPI/Atom XML, Avangrid/CMP CSV, Smart Meter Texas CSV, Green Button CSV, saved
header mappings, or CMP PDF/text. Importer names are `greenbutton-xml`, `csv-generic`, `bill-cmp`;
CSV labels identify the selected header profile. Avangrid uses offset-aware hour starts and kWh.
SMT uses local Central 15-minute intervals; Green Button CSV uses `SENSELESS_TZ`. Both profiles are
**unverified on real exports**. Ambiguous/nonexistent local DST timestamps are rejected rather than
guessed; offset timestamps remain distinct. Mappings support date/time columns, `strptime` formats,
IANA timezones and Wh conversion. Header matching is trimmed and case-insensitive.

An invalid row rejects the whole import. Normalized rows upsert on `(ts, seconds)` in settings.db's
`usage_interval` table (300–86400 seconds). `updated` includes unchanged rows. Duplicate keys use the
last row (SMT retains consumption and surplus separately). Existing `meter_hourly` rows migrate
idempotently with source `avangrid-csv`; all new analysis uses the interval store. Hourly analysis
chooses the finest observation over each covered segment; coarser observations fill only gaps.
Partial hours do not count as complete days. Unknown header labels are conservatively redacted in
previews as well as columns looking like names, addresses, account/ESIID/meter/service IDs.

Only dates, timestamps, kWh, temperatures, rates and charges are retained. Names, addresses, account,
service point, meter and invoice identifiers, original CSV/PDF bytes and extracted text are never
stored, returned or logged. CMP bills retain delivery segments (including prorated fixed charges and
kWh allowances), supply rate, taxes, bill date and current-charge totals. `amount` is delivery plus
supply, excluding prior balances/amount due. The period uses the meter's prior/read dates. Importing
or manually entering the same `(start, end)` updates that bill. `source` is `manual` or the bill importer NAME (`bill-cmp`); legacy `bill-pdf` sources migrate.
Normalized `lines` are persisted in `lines_json`, alongside all existing bill columns. `segments_json` contains only dates and numbers. For split bills without printed delivery rates, the
parser derives each rate from its rounded variable charge and kWh, so checks may differ by a few cents.
New manual entries snapshot the selected tariff (time-of-use needs entered on-peak kWh). Manual
periods before that tariff's published effective date are excluded from calibration.

Insights fields:

- `meter`: `{hours, from, to}`, with first/last Unix hour starts, or null.
- `sense_vs_meter`: `{days, meter_kwh, sense_kwh, ratio, months: [{month, meter_kwh, sense_kwh}]}`.
  `ratio` is Sense divided by meter. Uses complete local meter days also present in Sense month-scale
  history; excludes today and future days. DST days need 23 or 25 hours as appropriate.
- `plans_12mo`: `{from, to, months: [{month, kwh, on_peak_share, totals}], totals}`. Dates bound a
  half-open window of the last 12 complete calendar months. Needs at least 300 complete local meter
  days within that window. Each month's observed kWh is priced separately using all delivery/bundled plans in the selected pack and the selected supply; totals map every plan ID to dollars. Gaps are not extrapolated;
  fixed charges still apply to all 12 months. TOU costs use each plan's periods and the pack timezone, with the observed hourly profile. The legacy `on_peak_share`
  summary uses the first TOU plan. Holiday calendars are not inferred; the Python engine honors an
  explicit `holiday` flag on profile rows. CMP comparison retains its prior holiday approximation.
- `weather`: `{bins: [{from_f, to_f, days, kwh_per_day}]}`. Complete past days grouped into half-open
  10°F bins by mean available hourly temperature; days without temperatures are omitted.
- `bills`: `{start, end, kwh, amount, effective_cents_per_kwh, check}` entries. Imported bill `check`
  is `{model_total, diff}` (`diff = amount - model_total`), recomputed using that bill's own segments
  and supply rate: 5.5% on kWh above 750, apportioned to delivery segments by kWh, with no tax on fixed
  charges. Generic checks use the selected pack only when the full bill period is within the selected
  plan/supply effective dates and a sufficient TOU split is available. Legacy CMP manual bills without
  captured rates have null checks. CMP calibration still requires matching fixed/delivery rates;
  other packs calibrate only from manual bills with a matching component/supply/tax snapshot and a valid effective period.

Cycle comparison entries carry `basis` and the response carries `compare_basis`: `"12 months of meter
data"` uses **annual** totals/deltas from insights; `"last 7 days"` keeps projected cycle totals and
the recent on-peak share fallback. The projected bill itself continues to describe this cycle.
`projection_basis` is `"last year's meter data"` when every hour in last year's recent-seven-day and
remaining-cycle windows is available; otherwise it is `"last 7 days"`. `seasonal_factor` reports the
ratio of those windows' daily averages, clamped to 0.6–1.6 (null for fallback). Only remaining usage
is adjusted; missing elapsed days use the recent rate. Calibration excludes bills whose saved fixed
charge and delivery rates differ from the selected current tariff, and legacy bills with unknown rates.

## Floor plans

Floors, rooms and markers live in `settings.db`; images in `<data dir>/floorplans/`. Coordinates are
fractions (0–1) of the image's width and height. Design, validation rules and limits:
[floorplan-spec.md](floorplan-spec.md).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/floors` | None | Floors ordered by `ord, id`, each with `building_id`, `level`, `image_url` (or null), `img_w`, `img_h`, `rooms`, `markers`. |
| POST | `/api/floors` | `{name, building_id?, level?}` | The floor. At most 20 across the property. |
| PATCH | `/api/floors/{id}` | `{name?, ord?, building_id?, level?}` | The floor; `building_id` moves it with its rooms, markers and image. |
| DELETE | `/api/floors/{id}` | None | `{ok: true}`; deletes its rooms, markers and image. |
| PUT | `/api/floors/{id}/image` | Raw PNG/JPEG/WebP bytes, matching `Content-Type`, header `X-Senseless-Upload: 1` | The floor. Magic bytes must match; at most 15 MB and 12000 px per side. |
| GET | `/api/floors/{id}/image` | None | The image, `immutable` cache (the URL changes when it's replaced), `nosniff`. |
| POST | `/api/floors/{id}/rooms` | `{name, x, y, w, h}` | The room (must fit inside the image). |
| PATCH / DELETE | `/api/rooms/{id}` | Any of `{name, x, y, w, h}` / none | The room / `{ok: true}`. |
| POST | `/api/floors/{id}/markers` | `{kind: load\|sense\|label, ref?, label?, icon?, x, y}` | The marker. `load` refs a pattern name, `sense` a Sense device id. |
| PATCH / DELETE | `/api/markers/{id}` | Any of `{x, y, label, icon, ref, floor_id}` / none | The marker / `{ok: true}`. |

The image upload is not JSON: the custom header forces a CORS preflight, so other sites cannot send it,
and a supplied `Origin` must match the host as below.

## Property map and buildings

One property per install, stored alongside buildings and floors in `settings.db`. The startup migration
preserves existing floors, rooms, markers and image files, assigning legacy floors to a single
**Main house** building with levels 0, 1, 2… in their previous `ord, id` order. Repeated startup leaves
the hierarchy unchanged. See [property-spec.md](property-spec.md).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/property` | None | `{name, image, img_w, img_h, buildings}`; buildings ordered by `ord, id`, each with nested `floors` ordered by `level DESC, ord, id`. Floors include rooms and markers as in `/api/floors`. |
| PATCH | `/api/property` | `{name}` | The property with nested buildings and floors. Default name is `Home`. |
| PUT | `/api/property/image` | Raw PNG/JPEG/WebP, matching `Content-Type`, header `X-Senseless-Upload: 1` | The property. Same upload guards as floor images: magic/header validation, 15 MiB cap (including chunked bodies), maximum 12000 px per side, same-origin writes. |
| GET | `/api/property/image` | None | Site image; `Cache-Control: max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`; 404 if absent. |
| DELETE | `/api/property/image` | None | The property with image and dimensions cleared; removes the file. Safe to repeat. |
| POST | `/api/buildings` | `{name, icon?, x?, y?, w?, h?}` | The building: `{id, name, icon, ord, x, y, w, h, created}`. Defaults: icon `house`, ord 0, unplaced footprint (all four values null). |
| PATCH | `/api/buildings/{id}` | Any of `{name, icon, ord, x, y, w, h}` | The building. |
| DELETE | `/api/buildings/{id}` | None | `{ok: true}`; cascades floors, rooms and markers and removes all deleted floors' image files. The site image is retained. |

Property `image` is a versioned URL (`/api/property/image?v=<filename>`) or null. Replacing it removes
the previous file. Property images use server-generated `property-1-<random hex>.<ext>` basenames in
the same guarded image directory as floor images.

Names are trimmed, non-blank strings of at most 60 characters. Building icons: `house`, `garage`,
`barn`, `shed`, `greenhouse`, `pool`, `workshop`, `office`, `other`. Building `ord` is an integer
0–1000. A footprint must be either entirely null (unplaced) or a complete rectangle of finite numbers
0–1, with `w, h >= 0.02`, `x+w <= 1`, `y+h <= 1`. Patches validate the resulting rectangle; set all
four coordinates to null to unplace a building.

Floor `building_id` must identify an existing building. Omission on creation selects the first building
by `ord, id`, creating **Main house** if none exist. Levels are integers −5…20, unique per building;
duplicate levels return 400. An omitted create level uses the lowest free nonnegative level, then
−1…−5 if needed. Moving a floor retains its level unless a new one is supplied; if the destination
already has that level, supply a free `level` in the same patch. Unknown IDs return 404; invalid
fields, values and duplicate levels return 400. JSON and same-origin write rules below apply.

## Write protection

All POST and PATCH routes call `_body`. It requires `Content-Type: application/json`
(a charset parameter is accepted); otherwise it returns 415. Malformed JSON
returns 400. Send a JSON object with the documented fields.

POST, PATCH, PUT and DELETE routes reject a supplied `Origin` with 403 if the portion after
`://` differs from `req.host`, including its port. This is the code's same-origin
check: it compares host and port, not scheme. An absent Origin is accepted,
allowing command-line clients. DELETE routes do not require JSON or a body.

## Pages and static files

These routes are also registered in `web.py`. GET routes also receive aiohttp's
automatic HEAD handling.

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/`, `/timeline`, `/loads`, `/map`, `/history`, `/hunt` | None | Built frontend `index.html` with no-cache; if no frontend exists, the legacy status HTML. |
| GET | `/status` | None | Legacy status HTML. |
| GET, HEAD | `/assets/{path}` | Relative asset path | Static files, only when the frontend assets directory exists. |
| GET | `/manifest.webmanifest`, `/icon-192.png`, `/icon-512.png`, `/icon-maskable-512.png`, `/apple-touch-icon.png` | None | Corresponding file with `Cache-Control: max-age=86400`; each route is registered only if its file exists. |

The frontend directory is found through `SENSELESS_WEB_DIR` or `web/dist`.
