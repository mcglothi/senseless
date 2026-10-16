# Senseless's own HTTP API

These routes belong to the local Senseless server, not the [Sense cloud API](api.md).
This inventory is verified from [`senseless/web.py`](../senseless/web.py) and its
helpers. It is a code reference, not a report of live HTTP tests.

The server binds to `SENSELESS_HTTP_HOST` (default `0.0.0.0`) and
`SENSELESS_HTTP_PORT` (default `8780`). Responses are JSON unless stated otherwise.
There is no authentication in `make_app`. The write checks below are not login.

## Time windows

`/api/deltas`, `/api/series` and `/api/runs` accept Unix seconds `start` and `end`
for a half-open interval `[start, end)`. If either is supplied, `minutes` is ignored.
Missing `end` means now; missing `start` means the route's default span before end.
Otherwise, `minutes` selects a span ending now. A span over the route's cap moves
start forward. Non-numeric values or `end <= start` return 400.

| Route | Default span | Maximum span |
|---|---|---|
| `/api/deltas` | 10 minutes | 24 hours |
| `/api/series` | 30 minutes | 7 days |
| `/api/runs` | 180 minutes | 48 hours |

`/api/readings` accepts only `minutes` (default 10, capped at 360). It selects
`ts > now - minutes * 60`. Its helper checks numeric conversion and the upper cap,
but does not enforce positive minutes.

## Live data and history routes

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/healthz` | None | `{healthy, age}`. HTTP 200 when healthy, 503 otherwise. Age is seconds since the last realtime update, or null. |
| GET | `/metrics` | None | Prometheus text: feed health, power per leg, counters, database size; detection metrics when available: `senseless_load_running{load,legs}`, `senseless_load_energy_today_wh{load,legs}`, `senseless_patterns{state}`, `senseless_detection_age_seconds`. |
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

Stored pattern fields are `id`, `legs`, `stages`, `center_w`, `n`, `name`, `ignored`,
`created`, `named_at`, `last_seen`. Load responses also include `runs_window`,
`runs_today`, `wh_today`, `wh_window`, `last_ts`, `running`, `example`,
`median_duration_s`, `closed_runs`, `sense_hint`.
Run fields are `generation`, `start_frame`, `start_ts`, `end_frame`, `end_ts`,
`legs`, `w`, `off_w`, `stages`, `duration_s`, `running`, `status`, `wh`, `pattern`.
Invalid pattern IDs return 400; unknown IDs return 404.

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

## Floor plans

Floors, rooms and markers live in `settings.db`; images in `<data dir>/floorplans/`. Coordinates are
fractions (0–1) of the image's width and height. Design, validation rules and limits:
[floorplan-spec.md](floorplan-spec.md).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/floors` | None | Floors ordered by `ord, id`, each with `image_url` (or null), `img_w`, `img_h`, `rooms`, `markers`. |
| POST | `/api/floors` | `{name}` | The floor. At most 20. |
| PATCH | `/api/floors/{id}` | `{name?, ord?}` | The floor. |
| DELETE | `/api/floors/{id}` | None | `{ok: true}`; deletes its rooms, markers and image. |
| PUT | `/api/floors/{id}/image` | Raw PNG/JPEG/WebP bytes, matching `Content-Type`, header `X-Senseless-Upload: 1` | The floor. Magic bytes must match; at most 15 MB and 12000 px per side. |
| GET | `/api/floors/{id}/image` | None | The image, `immutable` cache (the URL changes when it's replaced), `nosniff`. |
| POST | `/api/floors/{id}/rooms` | `{name, x, y, w, h}` | The room (must fit inside the image). |
| PATCH / DELETE | `/api/rooms/{id}` | Any of `{name, x, y, w, h}` / none | The room / `{ok: true}`. |
| POST | `/api/floors/{id}/markers` | `{kind: load\|sense\|label, ref?, label?, icon?, x, y}` | The marker. `load` refs a pattern name, `sense` a Sense device id. |
| PATCH / DELETE | `/api/markers/{id}` | Any of `{x, y, label, icon, ref, floor_id}` / none | The marker / `{ok: true}`. |

The image upload is not JSON: the custom header forces a CORS preflight, so other sites cannot send it,
and a supplied `Origin` must match the host as below.

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
