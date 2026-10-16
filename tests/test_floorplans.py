import asyncio
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp.test_utils import TestClient, TestServer

from senseless import web
from senseless.floorplans import (
    FloorplanError, Floorplans, ICONS, MAX_IMAGE_BYTES, image_dimensions,
)
from senseless.state import LiveState


def png(w=3, h=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' * (w + 1)) * h)) + chunk(b'IEND', b''))


def jpeg(w=3, h=2, sof=0xc0):
    # A valid baseline grayscale image: one DC-zero/EOB block per 8x8 tile.
    def segment(marker, data):
        return bytes([255, marker]) + struct.pack('>H', len(data) + 2) + data
    dht = b'\x01' + b'\0' * 15 + b'\0'  # one symbol with a one-bit code
    blocks = ((w + 7) // 8) * ((h + 7) // 8)
    bits = '00' * blocks
    bits += '1' * (-len(bits) % 8)
    scan = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)).replace(b'\xff', b'\xff\0')
    return (b'\xff\xd8' + segment(0xdb, b'\0' + b'\x01' * 64)
            + segment(sof, b'\x08' + struct.pack('>HH', h, w) + b'\x01\x01\x11\0')
            + segment(0xc4, b'\0' + dht + b'\x10' + dht)
            + segment(0xda, b'\x01\x01\0\0\x3f\0') + scan + b'\xff\xd9')


def webp(w=3, h=2, extended=False):
    # Lossless constant RGBA image, five single-symbol Huffman trees (no pixel bits).
    bits = []
    def put(n, count):
        bits.extend((n >> i) & 1 for i in range(count))
    put(0, 3)  # no transform, color cache or meta Huffman codes
    for value in (128, 64, 32, 255, 0):  # green, red, blue, alpha, distance
        put(1, 1)  # simple Huffman code
        put(0, 1)  # one symbol
        put(1, 1)  # symbol uses eight bits
        put(value, 8)
    stream = bytes(sum(bit << j for j, bit in enumerate(bits[i:i + 8])) for i in range(0, len(bits), 8))
    lossless = b'\x2f' + struct.pack('<I', (w - 1) | ((h - 1) << 14)) + stream
    def chunk(kind, data):
        return kind + struct.pack('<I', len(data)) + data + b'\0' * (len(data) % 2)
    payload = b'WEBP'
    if extended:
        payload += chunk(b'VP8X', b'\0' * 4 + (w - 1).to_bytes(3, 'little') + (h - 1).to_bytes(3, 'little'))
    payload += chunk(b'VP8L', lossless)
    return b'RIFF' + struct.pack('<I', len(payload)) + payload


@pytest.fixture
def fp(tmp_path):
    return Floorplans(tmp_path / 'settings.db', tmp_path)


@pytest.fixture
def floor(fp):
    return fp.add_floor({'name': ' Ground '})['id']


ROOM = dict(name=' Kitchen ', x=0.1, y=0.2, w=0.4, h=0.5)
MARKER = dict(kind='load', ref=' Microwave ', x=0.3, y=0.4)


def test_crud_and_persistence(fp, floor, tmp_path):
    assert fp.all() == [dict(id=floor, name='Ground', building_id=1, level=0, ord=0, image_url=None, img_w=None, img_h=None, rooms=[], markers=[])]
    other = fp.add_floor({'name': 'Upstairs'})['id']
    fp.update_floor(floor, {'name': ' Downstairs ', 'ord': 10})
    assert [f['id'] for f in fp.all()] == [other, floor]
    room = fp.add_room(floor, ROOM)
    assert room['name'] == 'Kitchen'
    assert fp.update_room(room['id'], {'x': 0.2, 'w': 0.8, 'name': ' Dining '})['name'] == 'Dining'
    marker = fp.add_marker(floor, MARKER)
    assert marker['ref'] == 'Microwave'
    assert marker['icon'] is None and marker['label'] is None
    changed = fp.update_marker(marker['id'], dict(x=1, y=0, label=' Oven ', icon='oven', ref='OVEN', floor_id=other))
    assert changed['floor_id'] == other and changed['label'] == 'Oven'
    assert Floorplans(tmp_path / 'settings.db', tmp_path).all() == fp.all()
    fp.remove_room(room['id'])
    fp.remove_marker(marker['id'])
    fp.remove_floor(other)
    assert fp.all()[0]['rooms'] == [] and fp.all()[0]['markers'] == []


@pytest.mark.parametrize('value', [None, 1, True, [], {}, '', '   ', 'a' * 61])
def test_invalid_names(fp, floor, value):
    for action in (lambda: fp.add_floor({'name': value}), lambda: fp.update_floor(floor, {'name': value}),
                   lambda: fp.add_room(floor, {**ROOM, 'name': value})):
        with pytest.raises(FloorplanError):
            action()
    room = fp.add_room(floor, ROOM)
    with pytest.raises(FloorplanError):
        fp.update_room(room['id'], {'name': value})


@pytest.mark.parametrize('field', ['x', 'y', 'w', 'h'])
@pytest.mark.parametrize('value', [None, True, False, '0.2', [], {}, -0.01, 1.01, float('nan'), float('inf'), -float('inf')])
def test_invalid_coordinates(fp, floor, field, value):
    with pytest.raises(FloorplanError):
        fp.add_room(floor, {**ROOM, field: value})
    room = fp.add_room(floor, ROOM)
    with pytest.raises(FloorplanError):
        fp.update_room(room['id'], {field: value})
    if field in ('x', 'y'):
        with pytest.raises(FloorplanError):
            fp.add_marker(floor, {**MARKER, field: value})
        marker = fp.add_marker(floor, MARKER)
        with pytest.raises(FloorplanError):
            fp.update_marker(marker['id'], {field: value})


@pytest.mark.parametrize('patch', [dict(w=0), dict(h=0), dict(x=.7), dict(y=.6), dict(w=.91), dict(h=.81)])
def test_room_bounds(fp, floor, patch):
    with pytest.raises(FloorplanError):
        fp.add_room(floor, {**ROOM, **patch})
    room = fp.add_room(floor, ROOM)
    with pytest.raises(FloorplanError):
        fp.update_room(room['id'], patch)


@pytest.mark.parametrize('value', [None, True, False, 1.0, -1, 1001, '1', [], {}])
def test_ord(fp, floor, value):
    with pytest.raises(FloorplanError):
        fp.update_floor(floor, {'ord': value})


@pytest.mark.parametrize('patch', [
    dict(kind=None), dict(kind='unknown'), dict(kind=[]), dict(ref=None), dict(ref=''),
    dict(ref=' '), dict(ref=3), dict(ref=True), dict(ref=[]), dict(ref='a' * 81),
    dict(label=3), dict(label=True), dict(label=[]), dict(label='a' * 61),
    dict(icon='bogus'), dict(icon=[]), dict(icon=True),
    dict(kind='label', label=None), dict(kind='label', label=''), dict(kind='label', label='  '),
])
def test_marker_validation(fp, floor, patch):
    with pytest.raises(FloorplanError):
        fp.add_marker(floor, {**MARKER, **patch})
    if 'kind' not in patch:
        marker = fp.add_marker(floor, MARKER)
        with pytest.raises(FloorplanError):
            fp.update_marker(marker['id'], patch)


def test_required_fields(fp, floor):
    with pytest.raises(FloorplanError):
        fp.add_floor({})
    for field in ROOM:
        with pytest.raises(FloorplanError):
            fp.add_room(floor, {k: v for k, v in ROOM.items() if k != field})
    for field in MARKER:
        with pytest.raises(FloorplanError):
            fp.add_marker(floor, {k: v for k, v in MARKER.items() if k != field})


def test_valid_boundaries_and_icons(fp, floor):
    fp.update_floor(floor, {'name': 'a' * 60, 'ord': 1000})
    fp.update_floor(floor, {'ord': 0})
    fp.add_room(floor, dict(name='a' * 60, x=0, y=0, w=1, h=1))
    for kind in ('sense', 'load'):
        for label in (None, '', '  ', 'a' * 60):
            fp.add_marker(floor, dict(kind=kind, ref='a' * 80, label=label, x=0, y=1))
    for icon in ICONS | {None}:
        fp.add_marker(floor, dict(kind='label', label=' Panel ', x=1, y=0, icon=icon))
    marker = fp.add_marker(floor, dict(kind='label', label='Panel', x=0, y=0))
    for label in (None, '', 'a' * 61):
        with pytest.raises(FloorplanError):
            fp.update_marker(marker['id'], {'label': label})


@pytest.mark.parametrize('value', [None, True, False, 1.0, '1', [], {}, -1, 0])
def test_invalid_floor_move(fp, floor, value):
    marker = fp.add_marker(floor, MARKER)
    with pytest.raises(FloorplanError):
        fp.update_marker(marker['id'], {'floor_id': value})


def test_unknown_fields(fp, floor):
    room = fp.add_room(floor, ROOM)
    marker = fp.add_marker(floor, MARKER)
    for action in (lambda: fp.add_floor({'name': 'Floor', 'ord': 1}),
                   lambda: fp.update_floor(floor, {'image': '../oops'}),
                   lambda: fp.add_room(floor, {**ROOM, 'floor_id': floor}),
                   lambda: fp.update_room(room['id'], {'floor_id': floor}),
                   lambda: fp.add_marker(floor, {**MARKER, 'created': 0}),
                   lambda: fp.update_marker(marker['id'], {'kind': 'label'})):
        with pytest.raises(FloorplanError):
            action()


def test_unknown_ids(fp, floor):
    for action in (lambda: fp.update_floor(999, {}), lambda: fp.remove_floor(999),
                   lambda: fp.add_room(999, ROOM), lambda: fp.update_room(999, {}), lambda: fp.remove_room(999),
                   lambda: fp.add_marker(999, MARKER), lambda: fp.update_marker(999, {}), lambda: fp.remove_marker(999),
                   lambda: fp.image(999), lambda: fp.image(floor), lambda: fp.put_image(999, png(), 'image/png')):
        with pytest.raises(KeyError):
            action()
    marker = fp.add_marker(floor, MARKER)
    with pytest.raises(KeyError):
        fp.update_marker(marker['id'], {'floor_id': 999})


def test_limits(fp, floor):
    for i in range(25):                     # one building: levels -5..20 (the fixture made level 0)
        fp.add_floor({'name': str(i)})
    with pytest.raises(FloorplanError):     # every level taken: a clean error, not a crash
        fp.add_floor({'name': 'Overflow'})
    other = fp.add_building({'name': 'Barn', 'icon': 'barn'})['id']
    for i in range(26):
        fp.add_floor({'name': f'b{i}', 'building_id': other})
    third = fp.add_building({'name': 'Shed', 'icon': 'shed'})['id']
    for i in range(8):                      # 60 floors property-wide
        fp.add_floor({'name': f's{i}', 'building_id': third})
    with pytest.raises(FloorplanError):
        fp.add_floor({'name': 'Overflow', 'building_id': third})
    for i in range(100):
        room = fp.add_room(floor, ROOM)
    fp.update_room(room['id'], {'name': 'Still allowed'})
    with pytest.raises(FloorplanError):
        fp.add_room(floor, ROOM)
    for i in range(300):
        marker = fp.add_marker(floor, MARKER)
    fp.update_marker(marker['id'], {'x': 0})
    with pytest.raises(FloorplanError):
        fp.add_marker(floor, MARKER)
    other = fp.all()[1]['id']
    moving = fp.add_marker(other, MARKER)
    with pytest.raises(FloorplanError):
        fp.update_marker(moving['id'], {'floor_id': floor})
    assert fp.all()[1]['markers'][0]['id'] == moving['id']
    fp.remove_marker(marker['id'])
    fp.update_marker(moving['id'], {'floor_id': floor})


def test_concurrent_floor_limit(fp):
    buildings = [fp.add_building({'name': f'B{i}', 'icon': 'shed'})['id'] for i in range(4)]
    def add(i):
        try:
            fp.add_floor({'name': str(i), 'building_id': buildings[i % 4]})
            return True
        except FloorplanError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(add, range(80))) == 60


@pytest.mark.parametrize('make,ctype', [(png, 'image/png'), (jpeg, 'image/jpeg'), (webp, 'image/webp'),
                                       (lambda: webp(extended=True), 'image/webp')])
def test_image_upload_replace_cascade(fp, floor, make, ctype):
    room = fp.add_room(floor, ROOM)
    marker = fp.add_marker(floor, MARKER)
    first = fp.put_image(floor, make(), ctype)
    path = fp.image(floor)
    assert path.read_bytes() == make()
    assert first['img_w'] == 3 and first['img_h'] == 2
    assert first['image_url'] == f'/api/floors/{floor}/image?v={path.name}'
    assert path.parent == fp.image_dir
    fp.put_image(floor, png(), 'image/png')
    assert not path.exists() and fp.image(floor) != path
    path = fp.image(floor)
    fp.remove_floor(floor)
    assert not path.exists() and fp.all() == []
    with fp._conn() as db:
        assert db.execute('SELECT count(*) FROM rooms').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM markers').fetchone()[0] == 0
    with pytest.raises(KeyError):
        fp.update_room(room['id'], {})
    with pytest.raises(KeyError):
        fp.update_marker(marker['id'], {})


@pytest.mark.parametrize('ctype,data', [
    ('image/png', b'not an image'), ('image/png', jpeg()), ('image/jpeg', png()),
    ('image/webp', png()), ('image/svg+xml', b'<svg/>'), ('image/png', b'<svg/>'),
    ('image/png', b'\x89PNG\r\n\x1a\n'), ('image/jpeg', b'\xff\xd8\xff'),
    ('image/jpeg', b'\xff\xd8\xff\xc0\0\x01'), ('image/webp', b'RIFF\0\0\0\0WEBP'),
])
def test_bad_images(fp, floor, ctype, data):
    with pytest.raises(FloorplanError):
        fp.put_image(floor, data, ctype)
    assert list(fp.image_dir.iterdir()) == []


@pytest.mark.parametrize('make,ctype', [(png, 'image/png'), (jpeg, 'image/jpeg'), (webp, 'image/webp')])
@pytest.mark.parametrize('w,h', [(12001, 1), (1, 12001)])
def test_image_dimension_limit(fp, floor, make, ctype, w, h):
    with pytest.raises(FloorplanError):
        fp.put_image(floor, make(w, h), ctype)
    assert image_dimensions(make(12000, 1), ctype) == (12000, 1)


def test_jpeg_sof_and_webp_vp8_headers():
    for sof in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
        assert image_dimensions(jpeg(sof=sof), 'image/jpeg') == (3, 2)
    payload = b'VP8 ' + struct.pack('<I', 10) + b'\x10\0\0\x9d\x01\x2a' + struct.pack('<HH', 3, 2)
    data = b'RIFF' + struct.pack('<I', len(payload) + 4) + b'WEBP' + payload
    assert image_dimensions(data, 'image/webp') == (3, 2)
    for valid, ctype in ((png(), 'image/png'), (jpeg(), 'image/jpeg'), (webp(), 'image/webp')):
        # Every incomplete dimension header must be rejected without leaking parser errors.
        header_end = 33 if ctype == 'image/png' else valid.index(b'\xff\xc0') + 13 if ctype == 'image/jpeg' else len(valid)
        for end in range(header_end):
            with pytest.raises(FloorplanError):
                image_dimensions(valid[:end], ctype)


def test_path_traversal(fp, floor, tmp_path):
    target = tmp_path / 'keep.png'
    target.write_bytes(b'keep')
    for filename in ('../keep.png', str(target), 'floor-1-abc.png', '../floor-1-' + 'a' * 32 + '.png'):
        with fp._conn() as db:
            db.execute('UPDATE floors SET image=? WHERE id=?', (filename, floor))
        for action in (lambda: fp.image(floor), lambda: fp.remove_floor(floor), lambda: fp.put_image(floor, png(), 'image/png')):
            with pytest.raises(FloorplanError):
                action()
        assert target.read_bytes() == b'keep'
    name = 'floor-1-' + 'a' * 32 + '.png'
    (fp.image_dir / name).symlink_to(target)
    with fp._conn() as db:
        db.execute('UPDATE floors SET image=? WHERE id=?', (name, floor))
    with pytest.raises(FloorplanError):
        fp.image(floor)


def test_http_api(fp, tmp_path):
    async def run():
        async with TestClient(TestServer(web.make_app(LiveState(), tmp_path / 'data.db', floorplans=fp))) as client:
            async def json_write(method, path, body):
                response = await client.request(method, path, json=body)
                assert response.status == 200, await response.text()
                return await response.json()
            floor = await json_write('POST', '/api/floors', {'name': 'Floor'})
            base = f'/api/floors/{floor["id"]}'
            await json_write('PATCH', base, {'ord': 8})
            room = await json_write('POST', base + '/rooms', ROOM)
            marker = await json_write('POST', base + '/markers', MARKER)
            await json_write('PATCH', f'/api/rooms/{room["id"]}', {'name': 'Room'})
            await json_write('PATCH', f'/api/markers/{marker["id"]}', {'label': 'Label'})
            for make, ctype in ((png, 'image/png'), (jpeg, 'image/jpeg'), (webp, 'image/webp')):
                headers = {'Content-Type': ctype, 'X-Senseless-Upload': '1', 'Content-Disposition': 'attachment; filename="../../evil.svg"'}
                response = await client.put(base + '/image', data=make(), headers=headers)
                assert response.status == 200, await response.text()
                uploaded = await response.json()
                assert (uploaded['img_w'], uploaded['img_h']) == (3, 2)
                response = await client.get(uploaded['image_url'])
                assert response.status == 200 and await response.read() == make()
                assert response.headers['Cache-Control'] == 'max-age=31536000, immutable'
                assert response.headers['X-Content-Type-Options'] == 'nosniff'
                assert response.content_type == ctype
            response = await client.get('/api/floors')
            floors = await response.json()
            assert floors[0]['rooms'][0]['name'] == 'Room'
            assert floors[0]['markers'][0]['label'] == 'Label'
            for path in (f'/api/rooms/{room["id"]}', f'/api/markers/{marker["id"]}', base):
                response = await client.delete(path)
                assert response.status == 200 and await response.json() == {'ok': True}
            for method, path, body in [('PATCH', base, {}), ('DELETE', base, None), ('GET', base + '/image', None),
                                       ('PUT', base + '/image', None), ('POST', base + '/rooms', ROOM), ('POST', base + '/markers', MARKER),
                                       ('PATCH', '/api/rooms/999', {}), ('DELETE', '/api/rooms/999', None),
                                       ('PATCH', '/api/markers/999', {}), ('DELETE', '/api/markers/999', None)]:
                if method == 'PUT':
                    response = await client.put(path, data=png(), headers={'Content-Type': 'image/png', 'X-Senseless-Upload': '1'})
                else:
                    response = await client.request(method, path, **({'json': body} if body is not None else {}))
                assert response.status == 404, (method, path, await response.text())
            response = await client.patch('/api/floors/nope', json={})
            assert response.status == 400 and 'error' in await response.json()
    asyncio.run(run())


def test_http_write_security_and_upload_limits(fp, floor, tmp_path):
    async def run():
        async with TestClient(TestServer(web.make_app(LiveState(), tmp_path / 'data.db', floorplans=fp))) as client:
            base = f'/api/floors/{floor}'
            headers = {'Content-Type': 'image/png', 'X-Senseless-Upload': '1'}
            for extra, data, status in [({'X-Senseless-Upload': ''}, png(), 400),
                                        ({'Origin': 'https://foreign.invalid'}, png(), 403),
                                        ({'Content-Type': 'image/svg+xml'}, b'<svg/>', 400),
                                        ({}, b'wrong', 400), ({}, png(12001, 1), 400),
                                        ({}, b'x' * (MAX_IMAGE_BYTES + 1), 400)]:
                response = await client.put(base + '/image', data=data, headers={**headers, **extra})
                assert response.status == status
                if status == 400:
                    assert 'error' in await response.json()
            async def oversized_chunks():
                for _ in range(MAX_IMAGE_BYTES // 65536 + 1):
                    yield b'x' * 65536
            response = await client.put(base + '/image', data=oversized_chunks(), headers=headers)
            assert response.status == 400 and 'error' in await response.json()
            response = await client.put(base + '/image', data=png(), headers={'Content-Type': 'image/png'})
            assert response.status == 400
            same_origin = str(client.make_url('/')).rstrip('/')
            response = await client.put(base + '/image', data=png(), headers={**headers, 'Origin': same_origin})
            assert response.status == 200
            # JSON writes use the existing content-type and Origin protection.
            room = fp.add_room(floor, ROOM)
            marker = fp.add_marker(floor, MARKER)
            for method, path in [('POST', '/api/floors'), ('PATCH', base), ('POST', base + '/rooms'), ('POST', base + '/markers'),
                                  ('PATCH', f'/api/rooms/{room["id"]}'), ('PATCH', f'/api/markers/{marker["id"]}')]:
                response = await client.request(method, path, data='{}', headers={'Content-Type': 'text/plain'})
                assert response.status == 415
                response = await client.request(method, path, json={}, headers={'Origin': 'https://foreign.invalid'})
                assert response.status == 403
                response = await client.request(method, path, json=[])
                assert response.status == 400
                response = await client.request(method, path, data='{', headers={'Content-Type': 'application/json'})
                assert response.status == 400
            for path in (base, f'/api/rooms/{room["id"]}', f'/api/markers/{marker["id"]}'):
                response = await client.delete(path, headers={'Origin': 'https://foreign.invalid'})
                assert response.status == 403
            response = await client.post('/api/floors', json={'name': ''})
            assert response.status == 400 and 'error' in await response.json()
    asyncio.run(run())


def test_routes_without_network(fp, tmp_path):
    """Exercise registered handlers directly as well as through the HTTP integration tests."""
    import json
    from unittest.mock import Mock

    from aiohttp import StreamReader
    from aiohttp.test_utils import make_mocked_request
    from aiohttp.web import HTTPException

    async def run():
        app = web.make_app(LiveState(), tmp_path / 'data.db', floorplans=fp)

        async def request(method, path, *, body=None, data=b'', headers=None, payload=None):
            headers = {'Host': 'localhost', **(headers or {})}
            if body is not None:
                headers.setdefault('Content-Type', 'application/json')
                data = json.dumps(body).encode()
            if payload is None:
                protocol = Mock(_reading_paused=False)
                payload = StreamReader(protocol, 65536, loop=asyncio.get_running_loop())
                payload.feed_data(data)
                payload.feed_eof()
            req = make_mocked_request(method, path, headers=headers, payload=payload, app=app)
            match = await app.router.resolve(req)
            req._match_info = match
            try:
                return await match.handler(req)
            except HTTPException as e:
                return e

        def decoded(response, status=200):
            assert response.status == status, response.text
            return json.loads(response.text)

        floor = decoded(await request('POST', '/api/floors', body={'name': 'Floor'}))
        base = f'/api/floors/{floor["id"]}'
        decoded(await request('PATCH', base, body={'ord': 2}))
        room = decoded(await request('POST', base + '/rooms', body=ROOM))
        marker = decoded(await request('POST', base + '/markers', body=MARKER))
        decoded(await request('PATCH', f'/api/rooms/{room["id"]}', body={'name': 'Room'}))
        decoded(await request('PATCH', f'/api/markers/{marker["id"]}', body={'x': 1}))
        floors = decoded(await request('GET', '/api/floors'))
        assert floors[0]['rooms'][0]['name'] == 'Room' and floors[0]['markers'][0]['x'] == 1
        upload_headers = {'X-Senseless-Upload': '1', 'Content-Type': 'image/png', 'Origin': 'http://localhost'}
        for make, ctype in ((png, 'image/png'), (jpeg, 'image/jpeg'), (webp, 'image/webp')):
            result = decoded(await request('PUT', base + '/image', data=make(), headers={**upload_headers, 'Content-Type': ctype}))
            assert (result['img_w'], result['img_h']) == (3, 2)
            response = await request('GET', result['image_url'])
            assert response.status == 200 and response._path.read_bytes() == make()
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            assert response.headers['Cache-Control'] == 'max-age=31536000, immutable'
        for headers in ({'Content-Type': 'image/png'}, {**upload_headers, 'X-Senseless-Upload': '0'},
                        {**upload_headers, 'Content-Type': 'image/svg+xml'}):
            assert 'error' in decoded(await request('PUT', base + '/image', data=png(), headers=headers), 400)
        for method, path in [('PUT', base + '/image'), ('DELETE', base), ('DELETE', f'/api/rooms/{room["id"]}'),
                              ('DELETE', f'/api/markers/{marker["id"]}'), ('POST', '/api/floors'), ('PATCH', base),
                              ('POST', base + '/rooms'), ('POST', base + '/markers'),
                              ('PATCH', f'/api/rooms/{room["id"]}'), ('PATCH', f'/api/markers/{marker["id"]}')]:
            response = await request(method, path, body={}, headers={**upload_headers, 'Content-Type': 'application/json', 'Origin': 'https://evil.invalid'})
            assert response.status == 403
        for data in (b'bad', b'<svg/>', png(12001, 1)):
            assert 'error' in decoded(await request('PUT', base + '/image', data=data, headers=upload_headers), 400)

        class UnboundedUpload:
            chunks = 0

            async def iter_chunked(self, size):
                assert size <= 65536
                while True:
                    self.chunks += 1
                    # A handler that continues consuming after the cap fails this test.
                    assert self.chunks <= MAX_IMAGE_BYTES // size + 1
                    yield b'x' * size

        payload = UnboundedUpload()
        decoded(await request('PUT', base + '/image', headers=upload_headers, payload=payload), 400)
        assert payload.chunks == MAX_IMAGE_BYTES // 65536 + 1
        payload = UnboundedUpload()
        decoded(await request('PUT', base + '/image', headers={**upload_headers, 'Content-Length': str(MAX_IMAGE_BYTES + 1)}, payload=payload), 400)
        assert payload.chunks == 0
        for method, path in [('POST', '/api/floors'), ('PATCH', base), ('POST', base + '/rooms'), ('POST', base + '/markers'),
                              ('PATCH', f'/api/rooms/{room["id"]}'), ('PATCH', f'/api/markers/{marker["id"]}')]:
            assert (await request(method, path, data=b'{}', headers={'Content-Type': 'text/plain'})).status == 415
            for data in (b'[]', b'null', b'{'):
                assert 'error' in decoded(await request(method, path, data=data, headers={'Content-Type': 'application/json'}), 400)
        for path in (f'/api/rooms/{room["id"]}', f'/api/markers/{marker["id"]}', base):
            assert decoded(await request('DELETE', path)) == {'ok': True}
            assert (await request('DELETE', path)).status == 404
        for method, path, body in [('PATCH', base, {}), ('GET', base + '/image', None), ('POST', base + '/rooms', ROOM),
                                   ('POST', base + '/markers', MARKER), ('PATCH', '/api/rooms/999', {}), ('PATCH', '/api/markers/999', {})]:
            assert (await request(method, path, body=body)).status == 404
        assert (await request('PUT', base + '/image', data=png(), headers=upload_headers)).status == 404
        assert 'error' in decoded(await request('PATCH', '/api/floors/nope', body={}), 400)
    asyncio.run(run())


def test_out_of_range_ids(fp):
    for ident in (0, -1, 2**63, 10**100):
        for action in (lambda: fp.update_floor(ident, {}), lambda: fp.remove_floor(ident),
                       lambda: fp.update_room(ident, {}), lambda: fp.remove_room(ident),
                       lambda: fp.update_marker(ident, {}), lambda: fp.remove_marker(ident),
                       lambda: fp.image(ident)):
            with pytest.raises(KeyError):
                action()


def test_failed_replace_preserves_image(fp, floor, monkeypatch):
    from senseless import floorplans

    monkeypatch.setattr(floorplans.secrets, 'token_hex', lambda n: 'a' * 32)
    original = fp.put_image(floor, png(), 'image/png')
    path = fp.image(floor)
    with pytest.raises(FileExistsError):
        fp.put_image(floor, png(1, 1), 'image/png')
    assert path.read_bytes() == png() and fp.all()[0] == original
    with pytest.raises(FloorplanError):
        fp.put_image(floor, b'invalid', 'image/png')
    assert path.exists() and fp.all()[0] == original
