"""Property hierarchy migration, validation, files and registered HTTP handlers."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request
from aiohttp.web import HTTPException

from senseless import web
from senseless.floorplans import FloorplanError, Floorplans, MAX_IMAGE_BYTES
from senseless.state import LiveState
from test_floorplans import MARKER, ROOM, jpeg, png, webp


@pytest.fixture
def fp(tmp_path):
    return Floorplans(tmp_path / 'settings.db', tmp_path)


def test_migrate_pre_buildings_database(tmp_path):
    """Use the shipped pre-buildings schema, sparse ords, ties and existing files."""
    database = tmp_path / 'settings.db'
    directory = tmp_path / 'floorplans'
    directory.mkdir()
    filename = 'floor-7-' + 'a' * 32 + '.png'
    image = directory / filename
    image.write_bytes(png())
    with sqlite3.connect(database) as db:
        db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE floors (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                ord INTEGER NOT NULL DEFAULT 0, image TEXT, img_w INTEGER, img_h INTEGER,
                created REAL NOT NULL);
            CREATE TABLE rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
                name TEXT NOT NULL, x REAL, y REAL, w REAL, h REAL, created REAL NOT NULL);
            CREATE TABLE markers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, ref TEXT, label TEXT, icon TEXT,
                x REAL NOT NULL, y REAL NOT NULL, created REAL NOT NULL);
            INSERT INTO floors VALUES (7, 'Upper', 900, NULL, NULL, NULL, 123);
            INSERT INTO floors VALUES (3, 'Ground', 4, NULL, NULL, NULL, 122);
            INSERT INTO floors VALUES (9, 'Middle', 4, NULL, NULL, NULL, 124);
            INSERT INTO floors VALUES (50, 'Deleted', 0, NULL, NULL, NULL, 120);
            DELETE FROM floors WHERE id=50;
            INSERT INTO rooms VALUES (8, 7, 'Kitchen', .1, .2, .4, .5, 125);
            INSERT INTO markers VALUES (5, 7, 'label', NULL, 'Panel', 'panel', .3, .4, 126);
        ''')
        db.execute('UPDATE floors SET image=?, img_w=3, img_h=2 WHERE id=7', (filename,))
        # Other settings sharing this DB must survive the rebuild.
        db.execute('CREATE TABLE unrelated (value TEXT)')
        db.execute("INSERT INTO unrelated VALUES ('keep')")
        before = {table: db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                  for table in ('floors', 'rooms', 'markers')}

    migrated = Floorplans(database, tmp_path)
    with migrated._conn() as db:
        columns = {r['name']: r for r in db.execute('PRAGMA table_info(floors)')}
        assert columns['building_id']['notnull'] and columns['level']['notnull']
        assert any(r['table'] == 'buildings' and r['from'] == 'building_id' and r['on_delete'] == 'CASCADE'
                   for r in db.execute('PRAGMA foreign_key_list(floors)'))
        for table in before:
            fields = 'id,name,ord,image,img_w,img_h,created' if table == 'floors' else '*'
            assert [tuple(r) for r in db.execute(f'SELECT {fields} FROM {table} ORDER BY id')] == before[table]
        assert db.execute('SELECT value FROM unrelated').fetchone()[0] == 'keep'
        assert not list(db.execute('PRAGMA foreign_key_check'))
        assert db.execute('SELECT created FROM floors WHERE id=7').fetchone()[0] == 123

    prop = migrated.property()
    assert prop['name'] == 'Home' and prop['image'] is None
    assert len(prop['buildings']) == 1
    building = prop['buildings'][0]
    assert building['name'] == 'Main house' and building['icon'] == 'house'
    assert [(f['id'], f['level']) for f in building['floors']] == [(7, 2), (9, 1), (3, 0)]
    assert building['floors'][0]['rooms'][0]['id'] == 8
    assert building['floors'][0]['markers'][0]['id'] == 5
    assert [f['id'] for f in migrated.all()] == [3, 9, 7]
    assert migrated.image(7) == image and image.read_bytes() == png()
    migrated.update_property({'name': 'My home'})
    snapshot = migrated.property()
    for _ in range(2):
        migrated = Floorplans(database, tmp_path)
        assert migrated.property() == snapshot
        assert list(directory.iterdir()) == [image]
    # AUTOINCREMENT must not reuse an ID deleted before migration.
    assert migrated.add_floor({'name': 'New'})['id'] > 50
    migrated.remove_building(building['id'])
    assert migrated.all() == [] and not image.exists()
    with migrated._conn() as db:
        assert db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM markers').fetchone()[0] == 0


def test_property_and_building_crud_order_defaults(fp, tmp_path):
    from senseless.floorplans import BUILDING_ICONS

    assert fp.property() == dict(name='Home', image=None, img_w=None, img_h=None, buildings=[])
    assert fp.update_property({'name': ' Farm '})['name'] == 'Farm'
    house = fp.add_building({'name': ' House '})
    garage = fp.add_building({'name': 'Garage', 'icon': 'garage', 'x': 0, 'y': 0, 'w': 1, 'h': 1})
    assert house['name'] == 'House' and house['icon'] == 'house'
    assert all(house[k] is None for k in ('x', 'y', 'w', 'h'))
    fp.update_building(house['id'], {'ord': 10})
    ground = fp.add_floor({'name': 'Ground'})
    assert ground['building_id'] == garage['id'] and ground['level'] == 0
    upper = fp.add_floor({'name': 'Upper', 'building_id': garage['id'], 'level': 3})
    next_floor = fp.add_floor({'name': 'Next'})
    assert next_floor['level'] == 1
    basement = fp.add_floor({'name': 'Basement', 'building_id': garage['id'], 'level': -5})
    fp.update_floor(ground['id'], {'ord': 1000})
    prop = fp.property()
    assert [b['id'] for b in prop['buildings']] == [garage['id'], house['id']]
    assert [f['id'] for f in prop['buildings'][0]['floors']] == [upper['id'], next_floor['id'], ground['id'], basement['id']]
    assert fp.all()[-1]['id'] == ground['id']  # flat endpoint retains ord ordering
    for icon in BUILDING_ICONS:
        fp.update_building(house['id'], {'icon': icon})
    assert fp.update_building(house['id'], {'name': 'a' * 60, 'ord': 1000, 'x': .98, 'y': .98, 'w': .02, 'h': .02})['w'] == .02
    fp.update_building(garage['id'], dict(x=None, y=None, w=None, h=None))
    assert Floorplans(tmp_path / 'settings.db', tmp_path).property() == fp.property()
    fp.remove_building(house['id'])
    fp.remove_building(garage['id'])
    assert fp.property()['buildings'] == []
    floor = fp.add_floor({'name': 'Fresh'})
    assert floor['level'] == 0 and fp.property()['buildings'][0]['name'] == 'Main house'


@pytest.mark.parametrize('patch', [
    {'name': ''}, {'name': ' '}, {'name': None}, {'name': True}, {'name': 'a' * 61},
    {'icon': 'plug'}, {'icon': None}, {'icon': []}, {'created': 1},
    {'x': None}, {'x': True}, {'y': float('nan')}, {'w': float('inf')},
    {'x': -.1}, {'y': 1.1}, {'w': .019}, {'h': 0}, {'w': .91}, {'h': .81},
])
def test_building_validation_is_atomic(fp, patch):
    body = dict(name='House', x=.1, y=.2, w=.4, h=.5)
    building = fp.add_building(body)
    snapshot = fp.property()
    with pytest.raises(FloorplanError):
        fp.add_building({**body, **patch})
    with pytest.raises(FloorplanError):
        fp.update_building(building['id'], patch)
    assert fp.property() == snapshot


@pytest.mark.parametrize('value', [None, True, 1.0, -1, 1001, '1'])
def test_building_ord(fp, value):
    building = fp.add_building({'name': 'House'})
    with pytest.raises(FloorplanError):
        fp.update_building(building['id'], {'ord': value})


def test_incomplete_footprints_and_property_validation(fp):
    building = fp.add_building({'name': 'House'})
    for action in (lambda: fp.add_building({'name': 'House', 'x': 0}),
                   lambda: fp.update_building(building['id'], {'w': .5})):
        with pytest.raises(FloorplanError):
            action()
    for body in ({'name': ''}, {'name': 'a' * 61}, {'name': None}, {'image': 'bad'}, [], None):
        with pytest.raises(FloorplanError):
            fp.update_property(body)


@pytest.mark.parametrize('field,values', [
    ('level', [None, True, 1.0, '1', -6, 21, [], float('nan')]),
    ('building_id', [None, True, 1.0, '1', 0, -1, 2**63, []]),
])
def test_floor_hierarchy_validation(fp, field, values):
    floor = fp.add_floor({'name': 'Ground'})
    for value in values:
        for action in (lambda: fp.add_floor({'name': 'Bad', field: value}),
                       lambda: fp.update_floor(floor['id'], {field: value})):
            with pytest.raises(FloorplanError):
                action()
    assert fp.all() == [floor]


def test_floor_move_clashes_and_cascade(fp):
    ground = fp.add_floor({'name': 'Ground'})
    second = fp.add_floor({'name': 'Upper', 'level': 20})
    garage = fp.add_building({'name': 'Garage'})
    garage_floor = fp.add_floor({'name': 'Garage', 'building_id': garage['id']})
    fp.add_room(ground['id'], ROOM)
    fp.add_marker(ground['id'], MARKER)
    fp.put_image(ground['id'], png(), 'image/png')
    image = fp.image(ground['id'])
    fp.put_image(second['id'], jpeg(), 'image/jpeg')
    second_image = fp.image(second['id'])
    fp.put_property_image(webp(), 'image/webp')
    site_image = fp.property_image()
    for body in ({'level': 20}, {'building_id': garage['id']}):
        with pytest.raises(FloorplanError, match='level already exists'):
            fp.update_floor(ground['id'], body)
    with pytest.raises(FloorplanError):
        fp.add_floor({'name': 'Duplicate', 'level': 0})
    for action in (lambda: fp.add_floor({'name': 'Bad', 'building_id': 999}),
                   lambda: fp.update_floor(ground['id'], {'building_id': 999}),
                   lambda: fp.update_building(999, {}), lambda: fp.remove_building(999)):
        with pytest.raises(KeyError):
            action()
    moved = fp.update_floor(ground['id'], {'building_id': garage['id'], 'level': -1})
    assert moved['rooms'] and moved['markers'] and fp.image(ground['id']) == image
    assert fp.add_floor({'name': 'Reuse free level'})['level'] == 0
    fp.remove_building(ground['building_id'])
    assert not second_image.exists() and image.exists() and site_image.exists()
    assert {f['id'] for f in fp.all()} == {ground['id'], garage_floor['id']}
    fp.remove_building(garage['id'])
    assert not image.exists() and site_image.exists() and fp.all() == []
    with fp._conn() as db:
        assert db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM markers').fetchone()[0] == 0


def test_unique_floor_levels_under_concurrent_writes(fp):
    building = fp.add_building({'name': 'House'})
    def add(_):
        try:
            return fp.add_floor({'name': 'Ground', 'building_id': building['id'], 'level': 0})
        except FloorplanError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len([f for f in pool.map(add, range(16)) if f]) == 1
    with fp._conn() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO floors (name,created,building_id,level) VALUES (?,?,?,?)', ('Dup', 0, building['id'], 0))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO floors (name,created,building_id,level) VALUES (?,?,?,?)', ('Null', 0, None, 1))


@pytest.mark.parametrize('make,ctype', [(png, 'image/png'), (jpeg, 'image/jpeg'), (webp, 'image/webp')])
def test_property_image_lifecycle(fp, tmp_path, make, ctype):
    prop = fp.put_property_image(make(), ctype)
    path = fp.property_image()
    assert prop['image'] == f'/api/property/image?v={path.name}'
    assert (prop['img_w'], prop['img_h']) == (3, 2)
    assert path.read_bytes() == make()
    assert Floorplans(tmp_path / 'settings.db', tmp_path).property_image() == path
    fp.put_property_image(png(4, 5), 'image/png')
    assert not path.exists()
    path = fp.property_image()
    cleared = fp.remove_property_image()
    assert cleared['image'] is None and cleared['img_w'] is None and cleared['img_h'] is None
    assert not path.exists() and fp.remove_property_image() == cleared
    with pytest.raises(KeyError):
        fp.property_image()


def test_property_image_failure_and_path_guards(fp, tmp_path, monkeypatch):
    fp.put_property_image(png(), 'image/png')
    original = fp.property()
    path = fp.property_image()
    for data, ctype in [(b'<svg/>', 'image/svg+xml'), (jpeg(), 'image/png'), (b'bad', 'image/png'),
                        (png(12001, 1), 'image/png'), (b'x' * (MAX_IMAGE_BYTES + 1), 'image/png')]:
        with pytest.raises(FloorplanError):
            fp.put_property_image(data, ctype)
        assert fp.property() == original and path.read_bytes() == png()
    # Failed exclusive create must preserve the old image and database fields.
    monkeypatch.setattr('senseless.floorplans.secrets.token_hex', lambda _: path.name.split('-')[-1].split('.')[0])
    with pytest.raises(FileExistsError):
        fp.put_property_image(png(1, 1), 'image/png')
    assert fp.property() == original and path.exists()
    outside = tmp_path / 'keep.png'
    outside.write_bytes(b'keep')
    symlink = fp.image_dir / ('property-1-' + 'b' * 32 + '.png')
    symlink.symlink_to(outside)
    for filename in ('../keep.png', str(outside), 'property-1-short.png', symlink.name):
        with fp._conn() as db:
            db.execute('UPDATE property SET image=? WHERE id=1', (filename,))
        for action in (fp.property_image, fp.remove_property_image,
                       lambda: fp.put_property_image(png(), 'image/png')):
            with pytest.raises(FloorplanError):
                action()
        assert outside.read_bytes() == b'keep' and path.exists()


def test_building_cascade_validates_all_paths_before_deleting(fp, tmp_path):
    floor = fp.add_floor({'name': 'Ground'})
    fp.put_image(floor['id'], png(), 'image/png')
    image = fp.image(floor['id'])
    bad = fp.add_floor({'name': 'Upper'})
    with fp._conn() as db:
        db.execute('UPDATE floors SET image=? WHERE id=?', ('../keep.png', bad['id']))
    with pytest.raises(FloorplanError):
        fp.remove_building(floor['building_id'])
    assert len(fp.all()) == 2 and image.exists()


def test_property_routes_without_network(fp, tmp_path):
    """Resolve the real routes and execute handlers, including streaming upload guards."""
    async def run():
        site = tmp_path / 'site'
        site.mkdir()
        (site / 'index.html').write_text('<html>app</html>')
        app = web.make_app(LiveState(), tmp_path / 'data.db', floorplans=fp, web_dir=site)

        async def request(method, path, *, body=None, data=b'', headers=None, payload=None):
            headers = {'Host': 'localhost', **(headers or {})}
            if body is not None:
                headers.setdefault('Content-Type', 'application/json')
                data = json.dumps(body).encode()
            if payload is None:
                payload = StreamReader(Mock(_reading_paused=False), 65536, loop=asyncio.get_running_loop())
                payload.feed_data(data)
                payload.feed_eof()
            req = make_mocked_request(method, path, headers=headers, payload=payload, app=app)
            req._match_info = await app.router.resolve(req)
            try:
                return await req._match_info.handler(req)
            except HTTPException as exc:
                return exc

        def decoded(response, status=200):
            assert response.status == status, response.text
            return json.loads(response.text)

        for path in ('/map', '/map/b/1', '/map/b/1/f/2'):
            response = await request('GET', path)
            assert response.status == 200 and response._path == site / 'index.html'
        assert decoded(await request('GET', '/api/property'))['name'] == 'Home'
        decoded(await request('PATCH', '/api/property', body={'name': 'Farm'}))
        building = decoded(await request('POST', '/api/buildings', body={'name': 'House'}))
        base = f'/api/buildings/{building["id"]}'
        decoded(await request('PATCH', base, body={'icon': 'barn', 'x': 0, 'y': 0, 'w': 1, 'h': 1}))
        floor = decoded(await request('POST', '/api/floors', body={'name': 'Floor', 'building_id': building['id'], 'level': -1}))
        decoded(await request('PATCH', f'/api/floors/{floor["id"]}', body={'level': 20}))
        assert decoded(await request('GET', '/api/property'))['buildings'][0]['floors'][0]['level'] == 20
        assert decoded(await request('GET', '/api/floors'))[0]['building_id'] == building['id']
        decoded(await request('POST', '/api/floors', body={'name': 'Clash', 'level': 20}), 400)
        upload = {'X-Senseless-Upload': '1', 'Content-Type': 'image/png', 'Origin': 'http://localhost'}
        for make, ctype in ((png, 'image/png'), (jpeg, 'image/jpeg'), (webp, 'image/webp')):
            prop = decoded(await request('PUT', '/api/property/image', data=make(), headers={**upload, 'Content-Type': ctype}))
            response = await request('GET', prop['image'])
            assert response.status == 200 and response._path.read_bytes() == make()
            assert response.headers['Cache-Control'] == 'max-age=31536000, immutable'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
        for extra, data in [({'X-Senseless-Upload': '0'}, png()), ({'Content-Type': 'image/svg+xml'}, b'<svg/>'),
                            ({}, b'bad'), ({}, jpeg()), ({}, png(12001, 1))]:
            decoded(await request('PUT', '/api/property/image', data=data, headers={**upload, **extra}), 400)

        class UnboundedUpload:
            chunks = 0

            async def iter_chunked(self, size):
                assert size <= 65536
                while True:
                    self.chunks += 1
                    assert self.chunks <= MAX_IMAGE_BYTES // size + 1
                    yield b'x' * size

        payload = UnboundedUpload()
        decoded(await request('PUT', '/api/property/image', headers=upload, payload=payload), 400)
        assert payload.chunks == MAX_IMAGE_BYTES // 65536 + 1
        payload = UnboundedUpload()
        decoded(await request('PUT', '/api/property/image', headers={**upload, 'Content-Length': str(MAX_IMAGE_BYTES + 1)}, payload=payload), 400)
        assert payload.chunks == 0
        for method, path in [('PATCH', '/api/property'), ('POST', '/api/buildings'), ('PATCH', base)]:
            assert (await request(method, path, body={}, headers={'Content-Type': 'text/plain'})).status == 415
            for data in (b'[]', b'null', b'{'):
                decoded(await request(method, path, data=data, headers={'Content-Type': 'application/json'}), 400)
        for method, path in [('PATCH', '/api/property'), ('POST', '/api/buildings'), ('PATCH', base),
                             ('DELETE', base), ('PUT', '/api/property/image'), ('DELETE', '/api/property/image')]:
            assert (await request(method, path, body={}, headers={**upload, 'Content-Type': 'application/json', 'Origin': 'https://evil.invalid'})).status == 403
        decoded(await request('PATCH', '/api/property', body={'name': ''}), 400)
        decoded(await request('POST', '/api/buildings', body={'name': 'Bad', 'icon': 'nope'}), 400)
        decoded(await request('PATCH', '/api/buildings/nope', body={}), 400)
        assert (await request('PATCH', '/api/buildings/999', body={})).status == 404
        prop = decoded(await request('DELETE', '/api/property/image'))
        assert prop['image'] is None and prop['buildings']
        assert (await request('GET', '/api/property/image')).status == 404
        assert decoded(await request('DELETE', base)) == {'ok': True}
        assert decoded(await request('GET', '/api/floors')) == []
        assert (await request('DELETE', base)).status == 404
    asyncio.run(run())
