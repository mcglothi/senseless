# Floor plans: spec (v1)

Upload a picture of each floor, place loads on it, draw rooms, and watch them light up live.
Design research: competitive survey of Home Assistant, SmartThings Map View, UniFi, openHAB, ioBroker,
Homey, SPAN, Floorplanner/RoomSketcher (2026-10-16). Decisions below.

## UX decisions

- **Two modes.** View (default): pan/zoom, tap a device for details; nothing moves by accident.
  Edit: drag devices from a palette, move/remove them, draw rooms, upload/replace the image.
- **Normalized coordinates** (0–1 of the image's width/height) for markers and rooms, so replacing the
  blueprint keeps placements and every screen size renders the same layout.
- **Calm live states** (no 30 pulsing icons): off = dim, no badge; on = full colour + steady glow + watts
  pill; heavy (≥ 1 kW) = warm aura + kW pill; only heavy loads breathe, slowly. Respects reduced motion.
- **Rooms** are rectangles in v1 (polygons later). A device inside a room counts toward it; the room shows
  live watts and today's energy.
- **Floors**: segmented switcher showing each floor's live load.
- **Tap → inspector** (side panel on desktop, bottom sheet on phones): live watts, today's energy, runs
  today, links to Loads/Timeline.
- **List view** alternative (floors → rooms → devices) for accessibility and quick scanning.
- Skipped in v1: 3D, AI wall tracing, scale calibration, polygon rooms, clustering.

## What a marker can be bound to

| kind | ref | live state comes from |
|---|---|---|
| `load` | a named load (pattern name, case-insensitive; groups of same-named patterns) | `/api/loads`: `running`, `center_w`, `wh_today` |
| `sense` | a Sense device id (from the realtime feed's `devices`) | `/api/stream` latest `devices[].w` |
| `label` | none | static label (e.g. "Panel", "Router") |

## Data model (settings.db)

```
floors  (id INTEGER PK, name TEXT NOT NULL, ord INTEGER NOT NULL DEFAULT 0, image TEXT, img_w INTEGER,
         img_h INTEGER, created REAL NOT NULL)
rooms   (id INTEGER PK, floor_id INTEGER NOT NULL, name TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL,
         created REAL NOT NULL)
markers (id INTEGER PK, floor_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT, label TEXT, icon TEXT,
         x REAL NOT NULL, y REAL NOT NULL, created REAL NOT NULL)
```

Images live in `<data dir>/floorplans/`, named `floor-<id>-<random hex>.<ext>`; replacing an image
deletes the old file. Deleting a floor deletes its rooms, markers and image.

## API

All writes: JSON bodies through the existing `_body()` (application/json + same-origin `Origin`), except
the image upload. Errors: 400 `{"error": "..."}` for validation, 404 for unknown ids.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/floors` | | `[{id, name, ord, image_url, img_w, img_h, rooms:[...], markers:[...]}]` ordered by `ord, id`; `image_url` is `/api/floors/{id}/image?v=<image filename>` or null |
| POST | `/api/floors` | `{name}` | the floor |
| PATCH | `/api/floors/{id}` | `{name?, ord?}` | the floor |
| DELETE | `/api/floors/{id}` | | `{ok: true}` |
| PUT | `/api/floors/{id}/image` | raw image bytes; `Content-Type: image/png\|image/jpeg\|image/webp`; header `X-Senseless-Upload: 1` | the floor |
| GET | `/api/floors/{id}/image` | | the image (`Cache-Control: max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`) |
| POST | `/api/floors/{id}/rooms` | `{name, x, y, w, h}` | the room |
| PATCH | `/api/rooms/{id}` | any of `{name, x, y, w, h}` | the room |
| DELETE | `/api/rooms/{id}` | | `{ok: true}` |
| POST | `/api/floors/{id}/markers` | `{kind, ref?, label?, icon?, x, y}` | the marker |
| PATCH | `/api/markers/{id}` | any of `{x, y, label, icon, ref, floor_id}` | the marker |
| DELETE | `/api/markers/{id}` | | `{ok: true}` |

### Validation

- names/labels: strings, trimmed, 1–60 chars (label may be empty/null for load/sense markers); `ref` ≤ 80
- coordinates: numbers (not bool) with `0 ≤ x, y ≤ 1`; rooms also `w, h > 0` and `x + w ≤ 1`, `y + h ≤ 1`
- `kind` ∈ {load, sense, label}; `load`/`sense` require `ref`; `icon` ∈ an allowlist (see below) or null
- `ord` integer 0–1000; at most 20 floors, 100 rooms and 300 markers per floor
- icon allowlist: `plug, bolt, fan, heater, ac, fridge, microwave, oven, washer, dryer, dishwasher, pump,
  water, light, tv, computer, router, car, tub, tool, panel, other`

### Image upload security

- `PUT` with a raw body, not multipart: needs the `X-Senseless-Upload: 1` header, which forces a CORS
  preflight, so another site cannot upload; `Origin`, when present, must match the host.
- At most 15 MB; content type must be png/jpeg/webp **and** the magic bytes must agree. No SVG (script).
- Dimensions are read from the header bytes (PNG IHDR, JPEG SOFn, WebP VP8/VP8L/VP8X), no new dependency;
  reject images over 12000 px on a side.
