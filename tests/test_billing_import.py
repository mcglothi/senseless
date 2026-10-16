"""Synthetic uploads only. Identity sentinels must never reach persistence or responses."""
import asyncio
import csv
import io
import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request
from aiohttp.web import HTTPException

from senseless import web
from senseless.billing import (
    DEFAULTS, MAX_IMPORT_BYTES, METER_HEADER, PLANS, Billing, BillingError,
    estimate, parse_cmp_bill_pdf, parse_cmp_bill_text,
)
from senseless.history import HistoryError
from senseless.state import LiveState

TZ = ZoneInfo('America/New_York')
PII = ('Pat Example', '1 Test Rd', 'TEST-ACCOUNT', 'TEST-SERVICE', 'TEST-METER', 'TEST-INVOICE')
SINGLE = '''
Pat Example
1 Test Rd
Account TEST-ACCOUNT Service point TEST-SERVICE Invoice TEST-INVOICE
Amount Due $999.99
Bill Date 10/01/2026
Delivery Charges: Residential ( 08/29/2026 - 09/29/2026 )
Delivery Service: 1,350 KWH
Up to 50 KWH @$27.37 +$27.37
1,300 KWH @$0.119590 +$155.46
Maine Sales Tax +$3.95
Total Current Delivery Charges $186.78
TEST-METER 09/29/2026 41,391 08/28/2026 40,041 32 1,350
Supplier: Pat Example
Energy Charge 1,350 KWH @$0.127210 +$171.73
Maine Sales Tax +$4.20
Total New Supplier Charges $175.93
'''
SPLIT = '''
Pat Example 1 Test Rd Account TEST-ACCOUNT Service TEST-SERVICE Invoice TEST-INVOICE
Bill Date 08/01/2026
Delivery Charges: Residential ( 06/27/2026 - 06/30/2026 )
Delivery Service:
Up to 6 KWH +$3.66
205 KWH +$27.98
Delivery Charges: Residential ( 07/01/2026 - 07/29/2026 )
Delivery Service:
Up to 44 KWH +$24.05
1,493 KWH +$178.55
Maine Sales Tax +$6.69
Total Current Delivery Charges $240.93
TEST-METER 07/29/2026 3,748 06/26/2026 2,000 33 1,748
Energy Charge 1,748 KWH @$0.127210 +$222.36
Maine Sales Tax +$6.98
Total New Supplier Charges $229.34
'''


def csv_bytes(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(METER_HEADER)
    for dt, kwh, temp in rows:
        writer.writerow([PII[0], PII[1], PII[2], PII[3], 'electric', dt[:10], dt, dt,
                         kwh, 'kWh', '', temp, kwh, 0])
    return out.getvalue().encode()


def pdf_bytes(text):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    lines = ['BT /F1 10 Tf 20 770 Td 12 TL']
    for line in text.splitlines():
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        lines.append(f'({escaped}) Tj T*')
    stream.set_data(('\n'.join(lines) + '\nET').encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def assert_private(billing, *outputs):
    with sqlite3.connect(billing.db_path) as db:
        persisted = '\n'.join(db.iterdump())
    artifacts = b''.join(p.read_bytes() for p in billing.db_path.parent.iterdir() if p.is_file())
    for sentinel in PII:
        assert sentinel not in persisted
        assert sentinel.encode() not in artifacts
        assert all(sentinel not in json.dumps(o) for o in outputs)


@pytest.fixture(autouse=True)
def timezone(monkeypatch):
    monkeypatch.setenv('SENSELESS_TZ', 'America/New_York')


def billing(tmp_path, history=None, now=datetime(2026, 10, 16, 12, tzinfo=TZ)):
    return Billing(tmp_path / 'settings.db', history=history, clock=lambda: now.timestamp())


def test_csv_dst_overlap_and_privacy(tmp_path, caplog):
    b = billing(tmp_path)
    rows = [('2025-11-02 00:00:00-04:00', 1, 30), ('2025-11-02 01:00:00-04:00', 2, ''),
            ('2025-11-02 01:00:00-05:00', 3, 32), ('2025-11-02 02:00:00-05:00', 4, 33)]
    result = b.import_data(b'\xef\xbb\xbf' + csv_bytes(rows))
    assert result == {'kind': 'meter-csv', 'added': 4, 'updated': 0,
                      'from': int(datetime.fromisoformat(rows[0][0]).timestamp()),
                      'to': int(datetime.fromisoformat(rows[-1][0]).timestamp())}
    stored = b._meter_rows()
    assert [r['ts'] for r in stored] == list(range(result['from'], result['to'] + 1, 3600))
    assert stored[1]['temp_f'] is None
    assert stored[2]['delivered'] == 3 and stored[2]['received'] == 0
    repeat = b.import_data(csv_bytes([rows[2], ('2025-11-02 02:00:00-05:00', 8, 35)]))
    assert (repeat['added'], repeat['updated']) == (0, 2)
    assert b._meter_rows()[-1]['kwh'] == 8
    assert_private(b, result, repeat, caplog.text)
    spring = b.import_data(csv_bytes([('2025-03-09 01:00:00-05:00', 1, 0), ('2025-03-09 03:00:00-04:00', 1, 0)]))
    assert spring['to'] - spring['from'] == 3600


def test_concurrent_imports_dedupe_across_connections(tmp_path):
    instances = [billing(tmp_path) for _ in range(4)]
    data = csv_bytes([('2025-01-01 00:00:00-05:00', 1, 32)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda b: b.import_data(data), instances))
    assert sum(r['added'] for r in results) == 1 and sum(r['updated'] for r in results) == 3
    values = parse_cmp_bill_text(SINGLE)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda b: b._save_bill(values), instances))
    assert len({r['id'] for r in results}) == 1
    assert len(instances[0].bills()) == 1


@pytest.mark.parametrize('transform', [
    lambda d: d.replace(b'kWh', b'Wh'),
    lambda d: d.replace(b'2025-01-01 00:00:00-05:00', b'2025-01-01 00:00:00'),
    lambda d: d.replace(b',1,kWh', b',nan,kWh'),
    lambda d: d.replace(b'Name,Address', b'Customer,Address'),
    lambda d: d + b'broken,row\n',
])
def test_csv_rejection_is_atomic_and_private(tmp_path, caplog, transform):
    b = billing(tmp_path)
    data = csv_bytes([('2025-01-01 00:00:00-05:00', 1, '')])
    with pytest.raises(BillingError) as error:
        b.import_data(transform(data))
    assert b._meter_rows() == []
    assert_private(b, str(error.value), caplog.text)


@pytest.mark.parametrize('text,total,kwh,segments', [(SINGLE, 362.71, 1350, 1), (SPLIT, 470.27, 1748, 2)], ids=['single', 'split'])
def test_bill_parser_and_own_rate_check(tmp_path, text, total, kwh, segments, caplog):
    parsed = parse_cmp_bill_text(text)
    assert parsed['amount'] == total and parsed['kwh'] == kwh
    parts = json.loads(parsed['segments_json'])
    assert len(parts) == segments
    b = billing(tmp_path)
    result = b.import_data(pdf_bytes(text))
    assert result['kind'] == 'bill-pdf' and result['source'] == 'bill-pdf'
    assert result['amount'] != 999.99
    assert result['start'] == ('2026-08-28' if segments == 1 else '2026-06-26')
    assert result['end'] == ('2026-09-29' if segments == 1 else '2026-07-29')
    if segments == 1:
        assert parts[0]['delivery_rate'] == .11959
    else:
        assert parts[0]['delivery_rate'] == pytest.approx(27.98 / 205)
    result2 = b.import_data(pdf_bytes(text))
    assert result2['id'] == result['id'] and len(b.bills()) == 1
    check = b.insights()['bills'][0]['check']
    assert check['model_total'] == pytest.approx(total, abs=.02)
    assert abs(check['diff']) <= .02
    # Checks survive changes to today's tariffs, exemption, and supply settings.
    b.update({'plan': 'custom', 'custom': {'fixed': 90, 'delivery': .5}, 'supply_per_kwh': .4, 'tax_rate': .1})
    assert b.insights()['bills'][0]['check'] == check
    assert_private(b, parsed, result, result2, b.insights(), caplog.text)


@pytest.mark.parametrize('text', [SINGLE.replace(' KWH', ' WH'), SINGLE.replace('Bill Date', 'Missing Date'),
                                SINGLE.replace('1,350\nSupplier', '1,351\nSupplier'), 'Pat Example 1 Test Rd'])
def test_bad_bill_text_never_echoed(text):
    with pytest.raises(BillingError) as error:
        parse_cmp_bill_text(text)
    assert all(s not in str(error.value) for s in PII)


def test_pdf_failure_never_logs_document(caplog):
    with caplog.at_level(logging.DEBUG), pytest.raises(BillingError):
        parse_cmp_bill_pdf(b'%PDF-Pat Example 1 Test Rd\nTEST-ACCOUNT\n')
    assert all(s not in caplog.text for s in PII)


def test_pdf_diagnostics_suppressed_only_for_import(monkeypatch, caplog):
    import pypdf

    def noisy_reader(*args):
        logging.getLogger('pypdf._reader').warning(PII[0])
        raise ValueError(PII[1])

    monkeypatch.setattr(pypdf, 'PdfReader', noisy_reader)
    with caplog.at_level(logging.WARNING), pytest.raises(BillingError) as error:
        parse_cmp_bill_pdf(b'%PDF-')
    assert PII[0] not in caplog.text and PII[1] not in str(error.value)
    logging.getLogger('pypdf._reader').warning('unrelated diagnostic')
    assert 'unrelated diagnostic' in caplog.text


def test_migrate_legacy_and_manual_upsert(tmp_path):
    path = tmp_path / 'settings.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE bills (id INTEGER PRIMARY KEY, start TEXT, end TEXT, kwh REAL, amount REAL, created REAL)')
        db.execute("INSERT INTO bills VALUES (1, '2026-01-01', '2026-02-01', 500, 150, 0)")
    b = billing(tmp_path)
    assert b.bills()[0]['source'] == 'manual'
    assert b.calibration() is None  # unknown historical rates cannot calibrate the current tariff
    assert b.insights()['bills'][0]['check'] is None
    body = {'start': '2026-01-01', 'end': '2026-02-01', 'kwh': 500, 'amount': 151, 'name': PII[0]}
    assert b.add_bill(body)['id'] == 1
    assert len(Billing(path).bills()) == 1
    assert_private(b, b.bills())


class DailyHistory:
    def __init__(self):
        self.calls = []

    def get(self, scale, when):
        self.calls.append((scale, when))
        d = date.fromisoformat(when)
        if scale == 'month':
            n = ((d + timedelta(days=32)).replace(day=1) - d).days
            return {'kwh': [48.0 if i != 2 else None for i in range(n)]}
        return {'kwh': [2.0] * 24}


def put_hours(b, start, end, usage=lambda dt: 1.0, temp=lambda dt: 35.0):
    # Step in Unix time so 23- and 25-hour local days are represented correctly.
    begin = int(datetime.combine(start, datetime.min.time(), TZ).timestamp())
    stop = int(datetime.combine(end, datetime.min.time(), TZ).timestamp())
    with b._conn() as db:
        db.executemany('INSERT OR REPLACE INTO meter_hourly VALUES (?,?,?,?,?)',
                       ((ts, usage(datetime.fromtimestamp(ts, TZ)), temp(datetime.fromtimestamp(ts, TZ)), None, None)
                        for ts in range(begin, stop, 3600)))


def test_insights_intersection_weather_and_today(tmp_path):
    h = DailyHistory()
    b = billing(tmp_path, h)
    put_hours(b, date(2026, 10, 1), date(2026, 10, 17), temp=lambda dt: -5 if dt.day <= 2 else 35)
    result = b.insights()
    assert result['meter']['hours'] == 16 * 24
    # One Sense day is missing and today is excluded.
    assert result['sense_vs_meter'] == {'days': 14, 'meter_kwh': 14 * 24, 'sense_kwh': 14 * 48, 'ratio': 2,
                                       'months': [{'month': '2026-10', 'meter_kwh': 14 * 24, 'sense_kwh': 14 * 48}]}
    assert result['weather'] == {'bins': [{'from_f': -10, 'to_f': 0, 'days': 2, 'kwh_per_day': 24},
                                         {'from_f': 30, 'to_f': 40, 'days': 13, 'kwh_per_day': 24}]}
    assert result['plans_12mo'] is None
    assert h.calls == [('month', '2026-10-01')]
    # Incomplete meter days cannot stand in for a full day's usage.
    with b._conn() as db:
        db.execute('DELETE FROM meter_hourly WHERE ts=(SELECT min(ts) FROM meter_hourly)')
    assert b.insights()['sense_vs_meter']['days'] == 13


def test_empty_insights_and_missing_weather(tmp_path):
    b = billing(tmp_path)
    assert b.insights() == dict(meter=None, sense_vs_meter=None, plans_12mo=None, weather=None, bills=[])
    put_hours(b, date(2025, 11, 2), date(2025, 11, 3), temp=lambda dt: None)
    out = b.insights()
    assert out['meter']['hours'] == 25
    assert out['weather'] is None


def test_year_plans_math_and_cycle_switch(tmp_path):
    b = billing(tmp_path, DailyHistory())
    fallback = b.cycle()
    assert fallback['compare_basis'] == 'last 7 days'
    # Last October through September; extra older and current-month data must not count.
    put_hours(b, date(2025, 9, 1), date(2026, 10, 17))
    year = b.insights()['plans_12mo']
    assert (year['from'], year['to']) == ('2025-10-01', '2026-10-01')
    assert len(year['months']) == 12
    # DST fall-back adds one hour to November; spring-forward removes one from March.
    months = {m['month']: m for m in year['months']}
    assert months['2025-11']['kwh'] == 30 * 24 + 1
    assert months['2026-03']['kwh'] == 31 * 24 - 1
    for month in year['months']:
        d = date.fromisoformat(month['month'] + '-01')
        end = (d + timedelta(days=32)).replace(day=1)
        weekdays = sum((d + timedelta(days=i)).weekday() < 5 for i in range((end - d).days))
        on = weekdays * 4
        assert month['on_peak_share'] == pytest.approx(on / month['kwh'], abs=1e-5)
        for pid in month['totals']:
            assert month['totals'][pid] == estimate(b.settings(), month['kwh'], on, pid)['total']
    assert set(year['totals']) == {'cmp-a', 'cmp-a-tou', 'cmp-et'}
    assert year['totals'] == {pid: round(sum(m['totals'][pid] for m in year['months']), 2) for pid in year['totals']}
    b.update({'plan': 'custom', 'custom': {'fixed': 30, 'delivery': .1}, 'supply_per_kwh': .2})
    updated = b.insights()['plans_12mo']
    assert 'custom' not in year['totals']            # an unused (all-zero) custom plan isn't compared
    assert updated['totals']['custom'] > 0
    cycle = b.cycle()
    assert cycle['compare_basis'] == '12 months of meter data'
    assert all(p['basis'] == cycle['compare_basis'] and p['total'] == updated['totals'][p['plan']]
               and p['delta'] == round(p['total'] - updated['totals']['custom'], 2) for p in cycle['compare'])


def test_year_requires_300_complete_days(tmp_path):
    b = billing(tmp_path)
    start = date(2025, 10, 1)
    put_hours(b, start, start + timedelta(days=299))
    assert b.insights()['plans_12mo'] is None
    put_hours(b, start + timedelta(days=299), start + timedelta(days=300))
    assert b.insights()['plans_12mo'] is not None


@pytest.mark.parametrize('ratio,expected', [(1.3, 1.3), (3, 1.6), (.2, .6)])
def test_seasonal_projection_and_missing_window(tmp_path, ratio, expected):
    b = billing(tmp_path, DailyHistory())
    baseline = b.cycle()['kwh_projected']
    put_hours(b, date(2025, 10, 9), date(2025, 11, 1), usage=lambda dt: 1 if dt.day < 16 else ratio)
    out = b.cycle()
    assert out['seasonal_factor'] == expected
    assert out['projection_basis'] == "last year's meter data"
    # Sense lacks October 3, so one missing day is filled at the unadjusted recent rate.
    assert out['kwh_projected'] == pytest.approx(15 * 48 + 48 + 48 * 15.5 * expected, abs=.1)
    with b._conn() as db:
        db.execute('DELETE FROM meter_hourly WHERE ts=(SELECT min(ts) FROM meter_hourly)')
    fallback = b.cycle()
    assert fallback['projection_basis'] == 'last 7 days' and fallback['kwh_projected'] == baseline


def test_calibration_uses_only_matching_tariffs(tmp_path):
    b = billing(tmp_path, DailyHistory())
    b.import_data(pdf_bytes(SINGLE))
    b.import_data(pdf_bytes(SPLIT))
    assert b.calibration() == 1
    b.update({'plan': 'custom', 'custom': {'fixed': 12, 'delivery': .2}})
    assert b.calibration() is None
    target = estimate(b.settings(), 900)['total']
    b.add_bill({'start': '2026-09-01', 'end': '2026-10-01', 'kwh': 900, 'amount': round(target * 1.1, 2)})
    assert b.calibration() == pytest.approx(1.1, abs=.001)
    b.update({'calibrate': True})
    out = b.cycle()
    assert out['projected']['calibrated_total'] == round(out['projected']['total'] * out['calibration'], 2)


def test_manual_calibration_captures_tou_and_rejects_older_periods(tmp_path):
    b = billing(tmp_path)
    b.add_bill({'start': '2026-01-01', 'end': '2026-02-01', 'kwh': 900, 'amount': 250})
    assert b.calibration() is None
    b.update({'plan': 'cmp-a-tou'})
    total = estimate(b.settings(), 900, 200)['total']
    b.add_bill({'start': '2026-09-01', 'end': '2026-10-01', 'kwh': 900, 'on_peak_kwh': 200, 'amount': total})
    assert b.calibration() == 1
    b.update({'plan': 'cmp-a'})
    assert b.calibration() is None


def test_import_routes_and_guards_without_sockets(tmp_path, caplog):
    async def run():
        b = billing(tmp_path, DailyHistory())
        app = web.make_app(LiveState(), tmp_path / 'data.db', billing=b)

        async def request(method, path, data=b'', headers=None, payload=None):
            headers = {'Host': 'localhost', **(headers or {})}
            if payload is None:
                payload = StreamReader(Mock(_reading_paused=False), 65536, loop=asyncio.get_running_loop())
                payload.feed_data(data)
                payload.feed_eof()
            req = make_mocked_request(method, path, headers=headers, payload=payload, app=app)
            req._match_info = await app.router.resolve(req)
            try:
                return await req.match_info.handler(req)
            except HTTPException as e:
                return e

        data = csv_bytes([('2025-01-01 00:00:00-05:00', 1, 32)])
        good = {'X-Senseless-Upload': '1', 'Origin': 'http://localhost', 'Content-Type': 'application/octet-stream'}
        for headers, status in [({}, 400), ({**good, 'X-Senseless-Upload': '0'}, 400),
                                ({**good, 'Origin': 'https://evil.invalid'}, 403)]:
            assert (await request('PUT', '/api/billing/import', data, headers)).status == status
        # Content, never filename or declared MIME, determines the parser.
        response = await request('PUT', '/api/billing/import', data, {**good, 'Content-Type': 'application/pdf'})
        assert response.status == 200 and json.loads(response.text)['kind'] == 'meter-csv'
        response = await request('PUT', '/api/billing/import', pdf_bytes(SINGLE), {**good, 'Content-Type': 'text/csv'})
        assert response.status == 200 and json.loads(response.text)['kind'] == 'bill-pdf'
        assert (await request('PUT', '/api/billing/import', b'Pat Example 1 Test Rd', good)).status == 400
        assert (await request('PUT', '/api/billing/import', b'', good)).status == 400
        # Declared oversize is refused before consuming any bytes.
        payload = Mock()
        result = await request('PUT', '/api/billing/import', headers={**good, 'Content-Length': str(MAX_IMPORT_BYTES + 1)}, payload=payload)
        assert result.status == 400
        payload.iter_chunked.assert_not_called()

        class Chunks:
            def __init__(self, count):
                self.count = count

            async def iter_chunked(self, size):
                for _ in range(self.count):
                    yield b'x' * size

        assert (await request('PUT', '/api/billing/import', headers=good, payload=Chunks(MAX_IMPORT_BYTES // 65536 + 1))).status == 400
        # Exactly the cap reaches parsing (and fails for invalid content), rather than the size guard.
        result = await request('PUT', '/api/billing/import', headers=good, payload=Chunks(MAX_IMPORT_BYTES // 65536))
        assert result.status == 400 and 'exceeds' not in result.text
        result = await request('GET', '/api/billing/insights')
        assert result.status == 200 and json.loads(result.text)['meter']['hours'] == 1
        assert_private(b, json.loads(result.text), caplog.text)
        index = {(e['method'], e['path']) for e in web.API_INDEX}
        assert ('PUT', '/api/billing/import') in index and ('GET', '/api/billing/insights') in index
        b.history = Mock()
        b.history.get.side_effect = HistoryError('private upstream failure')
        put_hours(b, date(2025, 1, 1), date(2025, 1, 2))
        result = await request('GET', '/api/billing/insights')
        assert result.status == 502 and 'private upstream failure' not in result.text
    asyncio.run(run())


def test_unconfigured_routes(tmp_path):
    async def run():
        app = web.make_app(LiveState(), tmp_path / 'data.db')
        for method, path in [('PUT', '/api/billing/import'), ('GET', '/api/billing/insights')]:
            req = make_mocked_request(method, path, app=app)
            req._match_info = await app.router.resolve(req)
            with pytest.raises(HTTPException) as exc:
                await req.match_info.handler(req)
            assert exc.value.status == 404
    asyncio.run(run())


def test_split_supply_segments_parse():
    """January bills split supply too (the Standard Offer changes on Jan 1): two Energy Charge lines."""
    from senseless.billing import parse_cmp_bill_text
    text = """Bill Date 01/31/2026
    Delivery Charges: Residential ( 12/31/2025 - 12/31/2025 )
    Delivery Service:
    Up to 2 KWH +$1.00
    54 KWH +$7.09
    Delivery Charges: Residential ( 01/01/2026 - 01/29/2026 )
    Delivery Service:
    Up to 48 KWH +$29.20
    1,581 KWH +$215.76
    Maine Sales Tax +$7.01
    Total Current Delivery Charges $260.06
    G000000 01/29/2026 29,578 12/30/2025 27,893 30 1,685
    New Supplier Charges
    Residential Service : ( 12/31/2025 - 12/31/2025 )
    Energy Charge 56 KWH @$0.106128 +$5.94
    Residential Service : ( 01/01/2026 - 01/29/2026 )
    Energy Charge 1,629 KWH @$0.127210 +$207.23
    Maine Sales Tax +$6.51
    Total New Supplier Charges $219.68"""
    bill = parse_cmp_bill_text(text)
    assert bill["kwh"] == 1685 and bill["amount"] == 479.74
    assert abs(bill["supply_rate"] * 1685 - (5.94 + 207.23)) < 0.01
