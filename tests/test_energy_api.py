"""Exercise the real aiohttp handlers without binding sandbox-blocked sockets."""
import asyncio
import json
from unittest.mock import Mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request
from aiohttp.web import HTTPException

from senseless import web
from senseless.billing import Billing, MAX_IMPORT_BYTES
from senseless.state import LiveState
from test_energy_importers import csv_data, private
from test_energy_tariff import pack


def test_energy_routes_crud_preview_privacy_and_guards(tmp_path):
    async def run():
        b = Billing(tmp_path / 'settings.db')
        app = web.make_app(LiveState(), tmp_path / 'data.db', billing=b)

        async def request(method, path, *, body=None, raw=b'', headers=None, payload=None):
            headers = {'Host': 'localhost', **(headers or {})}
            if body is not None:
                headers.setdefault('Content-Type', 'application/json')
                raw = json.dumps(body).encode()
            if payload is None:
                payload = StreamReader(Mock(_reading_paused=False), 65536, loop=asyncio.get_running_loop())
                payload.feed_data(raw)
                payload.feed_eof()
            req = make_mocked_request(method, path, headers=headers, payload=payload, app=app)
            req._match_info = await app.router.resolve(req)
            try:
                return await req.match_info.handler(req)
            except HTTPException as error:
                return error

        raw = csv_data(['Name', 'ESIID', 'When', 'Energy'], [
            ['Pat Example', 'FAKE-ESIID', f'2026-01-01T0{hour}:00:00+00:00', '1'] for hour in range(8)])
        good = {'X-Senseless-Upload': '1', 'Origin': 'http://localhost'}
        path = '/api/billing/import/preview'
        assert (await request('POST', path, raw=raw)).status == 400
        assert (await request('POST', path, raw=raw, headers={**good, 'Origin': 'http://evil.invalid'})).status == 403
        result = await request('POST', path, raw=raw, headers=good)
        unknown = json.loads(result.text)
        assert result.status == 200 and unknown['importer'] is None
        assert unknown['header'][:2] == ['[redacted]', '[redacted]']
        assert b.csv_profiles() == [] and b.usage.intervals() == []
        declared = Mock()
        result = await request('POST', path, headers={**good, 'Content-Length': str(MAX_IMPORT_BYTES + 1)}, payload=declared)
        assert result.status == 400
        declared.iter_chunked.assert_not_called()

        class TooLarge:
            async def iter_chunked(self, size):
                for _ in range(MAX_IMPORT_BYTES // size + 1):
                    yield b'x' * size
        assert (await request('POST', path, headers=good, payload=TooLarge())).status == 400

        mapping = {'name': 'test-export', 'match_headers': ['Name', 'ESIID', 'When', 'Energy'],
                   'columns': {'start': 'When', 'kwh': 'Energy'}, 'units': 'kWh'}
        profile_path = '/api/billing/csv-profiles'
        assert (await request('POST', profile_path, body=mapping, headers={'Origin': 'http://evil.invalid'})).status == 403
        assert (await request('POST', profile_path, raw=b'{}')).status == 415
        assert (await request('POST', profile_path, body=mapping)).status == 200
        assert json.loads((await request('GET', profile_path)).text) == [mapping]
        preview = json.loads((await request('POST', path, raw=raw, headers=good)).text)
        assert preview['kind'] == 'usage' and len(preview['sample']) == 5
        assert b.usage.intervals() == []
        imported = json.loads((await request('PUT', '/api/billing/import', raw=raw, headers=good)).text)
        assert imported['kind'] == 'meter-csv' and imported['added'] == 8 and imported['importer'] == 'csv-generic'
        private(b, unknown, preview, imported)
        assert (await request('DELETE', profile_path + '?name=test-export', headers={'Origin': 'http://evil.invalid'})).status == 403
        assert (await request('DELETE', profile_path + '?name=test-export')).status == 200
        assert (await request('DELETE', profile_path + '?name=test-export')).status == 404

        tariffs = '/api/billing/tariffs'
        assert json.loads((await request('GET', tariffs)).text)[0]['id'] == 'us-me-cmp'
        cmp = json.loads((await request('GET', tariffs + '/us-me-cmp')).text)
        assert cmp['timezone'] == 'America/New_York'
        assert (await request('POST', tariffs, body=cmp)).status == 400
        assert (await request('DELETE', tariffs + '/us-me-cmp')).status == 400
        assert (await request('GET', tariffs + '/missing')).status == 404
        assert (await request('DELETE', tariffs + '/missing')).status == 404
        example = pack([{'type': 'energy', 'rate': .25}])
        assert (await request('POST', tariffs, body=example, headers={'Origin': 'http://evil.invalid'})).status == 403
        assert (await request('POST', tariffs, body=example)).status == 200
        assert json.loads((await request('GET', tariffs + '/test-utility')).text) == example
        assert (await request('POST', '/api/billing', body={'pack': 'test-utility'})).status == 200
        result = json.loads((await request('GET', '/api/billing/estimate?kwh=100')).text)
        assert result['total'] == 25
        catalog = json.loads((await request('GET', '/api/billing')).text)
        assert catalog['plans']['test-plan']['components'] == example['plans'][0]['components']
        assert (await request('DELETE', tariffs + '/test-utility')).status == 400
        assert (await request('POST', '/api/billing', body={'pack': 'us-me-cmp'})).status == 200
        # Old plan identifiers and old response fields remain usable.
        old = json.loads((await request('GET', '/api/billing/estimate?kwh=550&plan=cmp-a')).text)
        assert old['plan'] == 'cmp-a' and old['total'] == 157.13
        assert (await request('DELETE', tariffs + '/test-utility', headers={'Origin': 'http://evil.invalid'})).status == 403
        assert (await request('DELETE', tariffs + '/test-utility')).status == 200
        assert (await request('POST', tariffs, body={'id': 'Pat Example'})).status == 400
        private(b, json.loads((await request('GET', '/api/billing/insights')).text))

        indexed = {(e['method'], e['path'].split('?')[0]) for e in web.API_INDEX}
        for route in app.router.routes():
            path = route.resource.canonical
            if path.startswith('/api/billing') and route.method != 'HEAD':
                assert (route.method, path) in indexed
    asyncio.run(run())


def test_unconfigured_energy_routes_are_404(tmp_path):
    async def run():
        app = web.make_app(LiveState(), tmp_path / 'data.db')
        for method, path in [('POST', '/api/billing/import/preview'), ('GET', '/api/billing/csv-profiles'),
                             ('POST', '/api/billing/csv-profiles'), ('DELETE', '/api/billing/csv-profiles?name=a'),
                             ('GET', '/api/billing/tariffs'), ('GET', '/api/billing/tariffs/a'),
                             ('POST', '/api/billing/tariffs'), ('DELETE', '/api/billing/tariffs/a')]:
            req = make_mocked_request(method, path, headers={'X-Senseless-Upload': '1', 'Content-Type': 'application/json'}, app=app)
            req._match_info = await app.router.resolve(req)
            with pytest.raises(HTTPException) as error:
                await req.match_info.handler(req)
            assert error.value.status == 404
    asyncio.run(run())
