"""Floor layouts and image storage, separate from the collector's database."""

import math
import re
import secrets
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from pathlib import Path


MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
ICONS = frozenset("plug bolt fan heater ac fridge microwave oven washer dryer dishwasher pump water light tv computer router car tub tool panel other".split())
BUILDING_ICONS = frozenset("house garage barn shed greenhouse pool workshop office other".split())


class FloorplanError(ValueError):
    pass


def image_dimensions(data: bytes, content_type: str) -> tuple[int, int]:
    """Read dimensions without decoding pixels; reject truncated/unsupported headers."""
    try:
        if content_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(data) < 33 or data[8:16] != b"\x00\x00\x00\rIHDR":
                raise ValueError
            w, h = struct.unpack_from(">II", data, 16)
        elif content_type == "image/jpeg" and data.startswith(b"\xff\xd8"):
            pos = 2
            while pos < len(data):
                if data[pos] != 0xff:
                    raise ValueError
                while pos < len(data) and data[pos] == 0xff:
                    pos += 1
                marker = data[pos]
                pos += 1
                if marker in (0xd9, 0xda, 0x00):
                    raise ValueError
                if marker == 0x01 or 0xd0 <= marker <= 0xd7:
                    continue
                size = struct.unpack_from(">H", data, pos)[0]
                if size < 2 or pos + size > len(data):
                    raise ValueError
                if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                    if size < 8 or size != 8 + 3 * data[pos + 7]:
                        raise ValueError
                    h, w = struct.unpack_from(">HH", data, pos + 3)
                    break
                pos += size
            else:
                raise ValueError
        elif content_type == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            end = struct.unpack_from("<I", data, 4)[0] + 8
            if end != len(data):
                raise ValueError
            pos = 12
            while pos + 8 <= end:
                kind = data[pos:pos + 4]
                size = struct.unpack_from("<I", data, pos + 4)[0]
                start = pos + 8
                if start + size + (size & 1) > end:
                    raise ValueError
                if kind == b"VP8X" and size == 10:
                    w = 1 + int.from_bytes(data[start + 4:start + 7], "little")
                    h = 1 + int.from_bytes(data[start + 7:start + 10], "little")
                    break
                if kind == b"VP8L" and size >= 5 and data[start] == 0x2f:
                    bits = int.from_bytes(data[start + 1:start + 5], "little")
                    if bits >> 29:
                        raise ValueError
                    w, h = (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
                    break
                if kind == b"VP8 " and size >= 10 and data[start] & 1 == 0 and data[start + 3:start + 6] == b"\x9d\x01\x2a":
                    w, h = struct.unpack_from("<HH", data, start + 6)
                    w, h = w & 0x3fff, h & 0x3fff
                    break
                pos = start + size + (size & 1)
            else:
                raise ValueError
        else:
            raise ValueError
    except (ValueError, IndexError, struct.error):
        raise FloorplanError("invalid image header or content type mismatch") from None
    if not (0 < w <= 12000 and 0 < h <= 12000):
        raise FloorplanError("image dimensions must be 1–12000 pixels per side")
    return w, h


class Floorplans:
    def __init__(self, settings_db: Path, data_dir: Path):
        self.db_path = Path(settings_db)
        self.image_dir = Path(data_dir) / "floorplans"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Rebuilding floors requires foreign keys off, before starting the transaction.
        # Child tables still reference the final floors name; no child rows are deleted.
        with self._lock, self._conn(foreign_keys=False) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS floors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    ord INTEGER NOT NULL DEFAULT 0, image TEXT, img_w INTEGER, img_h INTEGER,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS markers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, ref TEXT, label TEXT, icon TEXT,
                    x REAL NOT NULL, y REAL NOT NULL, created REAL NOT NULL);
            """)
            self._migrate(db)

    @staticmethod
    def _migrate(db):
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS buildings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            icon TEXT NOT NULL DEFAULT 'house', ord INTEGER NOT NULL DEFAULT 0,
            x REAL, y REAL, w REAL, h REAL, created REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS property (
            id INTEGER PRIMARY KEY CHECK (id=1), name TEXT NOT NULL DEFAULT 'Home',
            image TEXT, img_w INTEGER, img_h INTEGER)""")
        db.execute("INSERT OR IGNORE INTO property (id) VALUES (1)")
        columns = {r['name']: r for r in db.execute('PRAGMA table_info(floors)')}
        foreign_keys = list(db.execute('PRAGMA foreign_key_list(floors)'))
        if (not {'building_id', 'level'} <= columns.keys()
                or not columns['building_id']['notnull'] or not columns['level']['notnull']
                or not any(r['from'] == 'building_id' and r['table'] == 'buildings'
                           and r['on_delete'] == 'CASCADE' for r in foreign_keys)):
            rows = [dict(r) for r in db.execute('SELECT * FROM floors ORDER BY ord, id')]
            unassigned = [r for r in rows if r.get('building_id') is None]
            if unassigned:
                building = db.execute("""INSERT INTO buildings (name, created)
                    VALUES ('Main house', ?)""", (time.time(),)).lastrowid
                for level, row in enumerate(unassigned):
                    row.update(building_id=building, level=level)
            sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='floors'").fetchone()
            db.execute("""CREATE TABLE floors_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                ord INTEGER NOT NULL DEFAULT 0, image TEXT, img_w INTEGER, img_h INTEGER,
                created REAL NOT NULL,
                building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
                level INTEGER NOT NULL CHECK (typeof(level)='integer' AND level BETWEEN -5 AND 20))""")
            fields = ('id', 'name', 'ord', 'image', 'img_w', 'img_h', 'created', 'building_id', 'level')
            for row in rows:
                db.execute(f"INSERT INTO floors_new ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                           [row[k] for k in fields])
            db.execute('DROP TABLE floors')
            db.execute('ALTER TABLE floors_new RENAME TO floors')
            if sequence:
                db.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='floors'", (sequence[0],))
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS floors_building_level ON floors(building_id, level)')
        if list(db.execute('PRAGMA foreign_key_check')):
            raise FloorplanError('invalid floor plan references during migration')

    @contextmanager
    def _conn(self, *, foreign_keys=True):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=" + ('ON' if foreign_keys else 'OFF'))
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _row(db, table, ident):
        if type(ident) is not int or not 0 < ident <= 2**63 - 1:
            raise KeyError(ident)
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return dict(row)

    def _floor(self, db, ident):
        row = self._row(db, "floors", ident)
        filename = row.pop("image")
        row.pop("created")
        row["image_url"] = f"/api/floors/{ident}/image?v={filename}" if filename else None
        for table in ("rooms", "markers"):
            row[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE floor_id=? ORDER BY id", (ident,))]
        return row

    def all(self):
        with self._conn() as db:
            db.execute('BEGIN')
            return [self._floor(db, r[0]) for r in db.execute("SELECT id FROM floors ORDER BY ord, id")]

    def _property(self, db):
        row = self._row(db, 'property', 1)
        row.pop('id')
        filename = row['image']
        row['image'] = f'/api/property/image?v={filename}' if filename else None
        row['buildings'] = []
        for building in db.execute('SELECT * FROM buildings ORDER BY ord, id'):
            building = dict(building)
            building['floors'] = [self._floor(db, f[0]) for f in db.execute(
                'SELECT id FROM floors WHERE building_id=? ORDER BY level DESC, ord, id', (building['id'],))]
            row['buildings'].append(building)
        return row

    def property(self):
        with self._conn() as db:
            db.execute('BEGIN')
            return self._property(db)

    def update_property(self, body):
        self._fields(body, {'name'})
        with self._lock, self._conn() as db:
            if 'name' in body:
                db.execute('UPDATE property SET name=? WHERE id=1', (self._text(body['name'], 'name'),))
            return self._property(db)

    @staticmethod
    def _ord(value):
        if type(value) is not int or not 0 <= value <= 1000:
            raise FloorplanError('ord must be an integer between 0 and 1000')
        return value

    def _validate_building(self, row):
        row['name'] = self._text(row.get('name'), 'name')
        if not isinstance(row.get('icon'), str) or row['icon'] not in BUILDING_ICONS:
            raise FloorplanError('invalid building icon')
        row['ord'] = self._ord(row['ord'])
        fields = ('x', 'y', 'w', 'h')
        if all(row.get(k) is None for k in fields):
            return
        for k in fields:
            row[k] = self._number(row.get(k), k)
        if row['w'] < 0.02 or row['h'] < 0.02 or row['x'] + row['w'] > 1 or row['y'] + row['h'] > 1:
            raise FloorplanError('building dimensions must be at least 0.02 and fit inside image')

    def add_building(self, body):
        self._fields(body, {'name', 'icon', 'x', 'y', 'w', 'h'})
        row = dict(icon='house', ord=0, x=None, y=None, w=None, h=None)
        row.update(body)
        self._validate_building(row)
        with self._lock, self._conn() as db:
            self._limit(db, "buildings", 50)
            fields = ('name', 'icon', 'ord', 'x', 'y', 'w', 'h')
            cur = db.execute(f"INSERT INTO buildings ({','.join(fields)}, created) VALUES ({','.join('?' for _ in fields)}, ?)",
                             [row[k] for k in fields] + [time.time()])
            return self._row(db, 'buildings', cur.lastrowid)

    def update_building(self, ident, body):
        fields = ('name', 'icon', 'ord', 'x', 'y', 'w', 'h')
        self._fields(body, fields)
        with self._lock, self._conn() as db:
            row = self._row(db, 'buildings', ident)
            row.update(body)
            self._validate_building(row)
            db.execute(f"UPDATE buildings SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                       [row[k] for k in fields] + [ident])
            return self._row(db, 'buildings', ident)

    def remove_building(self, ident):
        with self._lock:
            with self._conn() as db:
                self._row(db, 'buildings', ident)
                paths = [self._path(r[0]) for r in db.execute(
                    'SELECT image FROM floors WHERE building_id=? AND image IS NOT NULL', (ident,))]
                db.execute('DELETE FROM buildings WHERE id=?', (ident,))
            for path in paths:
                path.unlink(missing_ok=True)

    @staticmethod
    def _fields(body, allowed):
        if not isinstance(body, dict) or body.keys() - set(allowed):
            raise FloorplanError("unsupported fields")

    @staticmethod
    def _text(value, field, limit=60, optional=False):
        if optional and value is None:
            return None
        if not isinstance(value, str):
            raise FloorplanError(f"{field} must be a string")
        value = value.strip()
        if len(value) > limit or (not optional and not value):
            raise FloorplanError(f"{field} must be 1–{limit} characters" if not optional else f"{field} must be at most {limit} characters")
        return value

    @staticmethod
    def _number(value, field):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1 or not math.isfinite(value):
            raise FloorplanError(f"{field} must be a number between 0 and 1")
        return value

    @staticmethod
    def _limit(db, table, maximum, floor_id=None):
        sql = f"SELECT COUNT(*) FROM {table}"
        count = db.execute(sql, ()).fetchone()[0] if floor_id is None else db.execute(sql + " WHERE floor_id=?", (floor_id,)).fetchone()[0]
        if count >= maximum:
            raise FloorplanError(f"at most {maximum} {table}" + (" per floor" if floor_id is not None else ""))

    def add_floor(self, body):
        self._fields(body, {"name", "building_id", "level"})
        name = self._text(body.get("name"), "name")
        with self._lock, self._conn() as db:
            self._limit(db, "floors", 60)          # property-wide: several buildings, a few floors each
            building = body.get('building_id')
            if 'building_id' not in body:
                first = db.execute('SELECT id FROM buildings ORDER BY ord, id LIMIT 1').fetchone()
                building = first[0] if first else db.execute(
                    "INSERT INTO buildings (name, created) VALUES ('Main house', ?)", (time.time(),)).lastrowid
            self._building_id(db, building)
            level = body['level'] if 'level' in body else self._next_level(db, building)
            self._level(db, building, level)
            cur = db.execute("INSERT INTO floors (name, building_id, level, created) VALUES (?,?,?,?)",
                             (name, building, level, time.time()))
            return self._floor(db, cur.lastrowid)

    def _building_id(self, db, value):
        if type(value) is not int or not 0 < value <= 2**63 - 1:
            raise FloorplanError('building_id must be a positive integer')
        self._row(db, 'buildings', value)

    @staticmethod
    def _next_level(db, building):
        used = {r[0] for r in db.execute('SELECT level FROM floors WHERE building_id=?', (building,))}
        # Ground upwards first; use basement levels if all above-ground levels are occupied.
        free = next((level for level in (*range(21), *range(-1, -6, -1)) if level not in used), None)
        if free is None:
            raise FloorplanError('this building has a floor on every level (-5 to 20)')
        return free

    @staticmethod
    def _level(db, building, value, ident=None):
        if type(value) is not int or not -5 <= value <= 20:
            raise FloorplanError('level must be an integer between -5 and 20')
        if db.execute('SELECT id FROM floors WHERE building_id=? AND level=? AND id!=?',
                      (building, value, ident or 0)).fetchone():
            raise FloorplanError('level already exists in this building')

    def update_floor(self, ident, body):
        self._fields(body, {"name", "ord", "building_id", "level"})
        with self._lock, self._conn() as db:
            row = self._row(db, "floors", ident)
            if "name" in body:
                row["name"] = self._text(body["name"], "name")
            if "ord" in body:
                row["ord"] = self._ord(body['ord'])
            row['building_id'] = body.get('building_id', row['building_id'])
            row['level'] = body.get('level', row['level'])
            self._building_id(db, row['building_id'])
            self._level(db, row['building_id'], row['level'], ident)
            db.execute("UPDATE floors SET name=?, ord=?, building_id=?, level=? WHERE id=?",
                       (row["name"], row["ord"], row['building_id'], row['level'], ident))
            return self._floor(db, ident)

    def _path(self, filename):
        # Only server-generated basenames may ever become filesystem paths.
        if not isinstance(filename, str) or not re.fullmatch(r"(?:floor-[0-9]+|property-1)-[0-9a-f]{32}\.(png|jpg|webp)", filename):
            raise FloorplanError("invalid stored image path")
        path = self.image_dir / filename
        if path.resolve().parent != self.image_dir.resolve() or path.is_symlink():
            raise FloorplanError("invalid stored image path")
        return path

    def remove_floor(self, ident):
        with self._lock:
            with self._conn() as db:
                row = self._row(db, "floors", ident)
                path = self._path(row["image"]) if row["image"] else None
                db.execute("DELETE FROM floors WHERE id=?", (ident,))
            if path:
                path.unlink(missing_ok=True)

    def _validate_child(self, table, row):
        for field in ("x", "y"):
            row[field] = self._number(row.get(field), field)
        if table == "rooms":
            row["name"] = self._text(row.get("name"), "name")
            for dim, coord in (("w", "x"), ("h", "y")):
                row[dim] = self._number(row.get(dim), dim)
                if row[dim] <= 0 or row[coord] + row[dim] > 1:
                    raise FloorplanError("room must have positive dimensions and fit inside image")
        else:
            if row.get("kind") not in ("load", "sense", "label"):
                raise FloorplanError("kind must be load, sense or label")
            row["ref"] = self._text(row.get("ref"), "ref", 80, optional=row["kind"] == "label")
            row["label"] = self._text(row.get("label"), "label", optional=row["kind"] != "label")
            if row.get("icon") is not None and (not isinstance(row["icon"], str) or row["icon"] not in ICONS):
                raise FloorplanError("invalid icon")
            row.setdefault("icon", None)

    def _child(self, table, body, *, floor_id=None, ident=None):
        fields = ("name", "x", "y", "w", "h") if table == "rooms" else ("kind", "ref", "label", "icon", "x", "y")
        allowed = fields if ident is None or table == "rooms" else ("ref", "label", "icon", "x", "y", "floor_id")
        self._fields(body, allowed)
        with self._lock, self._conn() as db:
            row = self._row(db, table, ident) if ident is not None else {"floor_id": floor_id}
            old_floor = row["floor_id"]
            row.update(body)
            target = row["floor_id"]
            if type(target) is not int or target <= 0:
                raise FloorplanError("floor_id must be a positive integer")
            self._row(db, "floors", target)
            self._validate_child(table, row)
            if ident is None or old_floor != target:
                self._limit(db, table, 100 if table == "rooms" else 300, target)
            columns = ("floor_id", *fields)
            if ident is None:
                cur = db.execute(f"INSERT INTO {table} ({','.join(columns)},created) VALUES ({','.join('?' for _ in columns)},?)", [row[k] for k in columns] + [time.time()])
                ident = cur.lastrowid
            else:
                db.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in columns)} WHERE id=?", [row[k] for k in columns] + [ident])
            return self._row(db, table, ident)

    def add_room(self, floor_id, body):
        return self._child("rooms", body, floor_id=floor_id)

    def update_room(self, ident, body):
        return self._child("rooms", body, ident=ident)

    def add_marker(self, floor_id, body):
        return self._child("markers", body, floor_id=floor_id)

    def update_marker(self, ident, body):
        return self._child("markers", body, ident=ident)

    def _remove_child(self, table, ident):
        with self._lock, self._conn() as db:
            self._row(db, table, ident)
            if not db.execute(f"DELETE FROM {table} WHERE id=?", (ident,)).rowcount:
                raise KeyError(ident)

    def remove_room(self, ident):
        self._remove_child("rooms", ident)

    def remove_marker(self, ident):
        self._remove_child("markers", ident)

    def put_image(self, ident, data, content_type):
        return self._put_image('floors', ident, data, content_type)

    def put_property_image(self, data, content_type):
        return self._put_image('property', 1, data, content_type)

    def _put_image(self, table, ident, data, content_type):
        if len(data) > MAX_IMAGE_BYTES:
            raise FloorplanError("image exceeds 15 MiB")
        w, h = image_dimensions(data, content_type)
        with self._lock:
            path = None
            created = False
            try:
                with self._conn() as db:
                    row = self._row(db, table, ident)
                    old = self._path(row["image"]) if row["image"] else None
                    prefix = 'floor' if table == 'floors' else 'property'
                    filename = f"{prefix}-{ident}-{secrets.token_hex(16)}.{IMAGE_TYPES[content_type]}"
                    path = self._path(filename)
                    with path.open("xb") as f:
                        created = True
                        f.write(data)
                    db.execute(f"UPDATE {table} SET image=?, img_w=?, img_h=? WHERE id=?", (filename, w, h, ident))
                    result = self._floor(db, ident) if table == 'floors' else self._property(db)
            except Exception:
                if created:
                    path.unlink(missing_ok=True)
                raise
            if old:
                old.unlink(missing_ok=True)
            return result

    def image(self, ident):
        return self._image('floors', ident)

    def property_image(self):
        return self._image('property', 1)

    def remove_property_image(self):
        with self._lock:
            with self._conn() as db:
                row = self._row(db, 'property', 1)
                path = self._path(row['image']) if row['image'] else None
                db.execute('UPDATE property SET image=NULL, img_w=NULL, img_h=NULL WHERE id=1')
                result = self._property(db)
            if path:
                path.unlink(missing_ok=True)
            return result

    def _image(self, table, ident):
        with self._conn() as db:
            row = self._row(db, table, ident)
            if not row["image"]:
                raise KeyError(ident)
            path = self._path(row["image"])
            if not path.is_file():
                raise KeyError(ident)
            return path
