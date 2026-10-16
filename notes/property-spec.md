# Property map: buildings, floors, rooms (spec)

Status: backend done; UI planned for 0.8.0. Extends the floor plans in `floorplan-spec.md`.

## Model

Property → Buildings → Floors → Rooms / Markers (devices). One property per install.

- **Property** (singleton settings row): optional site image (aerial photo or site plan) with the same
  upload rules as floor images; `name` (default "Home").
- **Building**: `id`, `name` (≤ 60), `icon` (allowlist: `house`, `garage`, `barn`, `shed`, `greenhouse`,
  `pool`, `workshop`, `office`, `other`), `ord`, and a footprint on the site canvas `x, y, w, h`
  (normalized 0–1, nullable until placed), `created`.
- **Floor** gains `building_id` (NOT NULL after migration, ON DELETE CASCADE) and `level` (integer,
  −5…20; 0 = ground, negative = basement, used for sorting and the floor switcher). `ord` stays as a
  tiebreaker.
- Rooms and markers are unchanged (they belong to a floor).

## Migration (idempotent, at startup)

- Create `buildings` and `property` if missing; add `floors.building_id` and `floors.level` if missing.
- If floors exist without a building: create one building "Main house" (icon `house`) and assign them,
  with `level` from their current `ord` (0, 1, 2… in order). No data loss; image files untouched.

## API

Existing floor/room/marker routes keep working. `POST /api/floors` gains optional `building_id` and
`level` (defaults: the first building, creating "Main house" if none; next free level). `PATCH
/api/floors/{id}` accepts `building_id` (move a floor between buildings) and `level`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/property` | | `{name, image, img_w, img_h, buildings: [{…building, floors: [{…floor, rooms, markers}]}]}` floors sorted by level desc (top floor first), buildings by `ord` |
| PATCH | `/api/property` | `{name}` | property |
| PUT | `/api/property/image` | raw image, header `X-Senseless-Upload: 1` | property |
| GET | `/api/property/image` | | the site image |
| DELETE | `/api/property/image` | | property (image removed) |
| POST | `/api/buildings` | `{name, icon?, x?, y?, w?, h?}` | building |
| PATCH | `/api/buildings/{id}` | any of `name, icon, ord, x, y, w, h` | building |
| DELETE | `/api/buildings/{id}` | | `{ok}`; cascades floors, rooms, markers and deletes their image files (the UI confirms first). |

`GET /api/floors` stays (flat list, each floor now carries `building_id` and `level`) for older clients.

Validation as in floorplans.py: names non-blank ≤ 60, footprint values finite 0–1 with x+w ≤ 1 and
y+h ≤ 1, w and h ≥ 0.02, icon in the allowlist, level integer in range and unique within a building
(409/400 on clash). Same-origin write rule and upload guards as the floor image routes.

Add every new route to `API_INDEX` in web.py (a test enforces it).

## UI (Claude builds this part)

- `/map` opens the **property view**: site image (or a blank grid) with building footprints showing live
  watts and today's kWh, tier glow as rooms do. Click a building → animated zoom into the **building
  view**: its floors (floor switcher sorted by level with live watts per level), rooms, markers, as the
  current Map. Breadcrumb Property › Building › Floor; URLs `/map`, `/map/b/{id}`, `/map/b/{id}/f/{id}`.
- **Outline** panel: tree of buildings → floors → rooms with counts and rollups; add/rename/reorder/move.
- Edit mode on the property view: draw/move/resize building footprints (same gestures as rooms).
- No site image: buildings as tiles on a grid until placed.
