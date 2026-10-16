"""Synthetic regressions for the eight billing/import review findings."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from senseless.billing import Billing, BillingError
from senseless.energy.importers import csv_generic, greenbutton_xml, import_data
from test_billing_import import DailyHistory, put_hours
from test_energy_importers import PII, csv_data, private, xml_data
from test_energy_tariff import pack, price


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'])
@pytest.mark.parametrize('declaration', [
    '<!DOCTYPE feed>',
    '<!DOCTYPE feed [<!ENTITY payload "' + 'x' * 1024 + '">]>',
    '<!DOCTYPE feed SYSTEM "file:///never-open-this">',
])
def test_review_1_xml_forbids_dtd_in_every_encoding(tmp_path, encoding, declaration):
    xml = xml_data().decode()
    title = '&payload;' * 16 if '<!ENTITY' in declaration else 'ordinary text'
    xml = declaration + xml.replace('<entry>', '<title>' + title + '</title><entry>', 1)
    data = xml.encode(encoding)
    # The parser itself must reject it; detection alone is not a security boundary.
    with pytest.raises(BillingError, match='invalid Green Button XML'):
        greenbutton_xml.parse(data)
    assert Billing(tmp_path / 's.db').usage.intervals() == []


def test_review_1_utf16_entity_payload_cannot_bypass_registry(tmp_path):
    # UTF-16 codepoints whose bytes contain the ASCII detector's signatures.
    signature = b'http://naesb.org/espi IntervalReading '
    text = signature.decode('utf-16-le')
    xml = '<!DOCTYPE feed [<!ENTITY p "' + 'x' * 1024 + '">]>' + xml_data().decode()
    xml = xml.replace('<entry>', '<title>' + text + '&p;' * 16 + '</title><entry>', 1)
    data = xml.encode('utf-16')
    assert greenbutton_xml.detect(data) > .5
    b = Billing(tmp_path / 's.db')
    for operation in (b.preview_import, b.import_data):
        with pytest.raises(BillingError, match='invalid Green Button XML'):
            operation(data)
    assert b.usage.intervals() == []


def test_review_1_normal_utf16_and_text_budget(monkeypatch):
    assert greenbutton_xml.parse(xml_data().decode().encode('utf-16'))['rows']
    monkeypatch.setattr(greenbutton_xml, 'MAX_XML_TEXT', 64)
    with pytest.raises(BillingError):
        greenbutton_xml.parse(xml_data())


@pytest.mark.parametrize('legacy', [False, True], ids=['save', 'migrate'])
def test_review_2_raw_matching_headers_become_hashes(tmp_path, legacy):
    b = Billing(tmp_path / 's.db')
    header = [PII[0], PII[1], 'When', 'Energy']
    data = csv_data(header, [['ignored', 'ignored', '2026-01-01T00:00:00+00:00', '2']])
    original = {'name': 'private-export', 'match_headers': header,
                'columns': {'start': 'When', 'kwh': 'Energy'}, 'units': 'kWh'}
    expected = {k: v for k, v in original.items() if k != 'match_headers'}
    expected['match_hash'] = csv_generic.header_hash(data)
    if legacy:
        with b._conn() as db:
            db.execute('INSERT INTO csv_profiles VALUES (?,?)', (original['name'], json.dumps(original)))
        b = Billing(b.db_path)
    else:
        assert b.save_csv_profile(original) == expected
        assert original['match_headers'] == header  # caller's value is not mutated
    assert b.csv_profiles() == [expected]
    assert Billing(b.db_path).csv_profiles() == [expected]  # idempotent
    assert b.preview_import(data)['sample'][0][2] == 2
    assert b.import_data(data)['added'] == 1
    private(b, b.csv_profiles())


@pytest.mark.parametrize('day,hour', [(date(2026, 3, 8), 17), (date(2026, 11, 1), 17)])
def test_review_3_live_tou_uses_wall_hours_on_dst_days(tmp_path, monkeypatch, day, hour):
    monkeypatch.setenv('SENSELESS_TZ', 'America/Chicago')
    b = Billing(tmp_path / 's.db')
    p = pack([{'type': 'tou', 'periods': [{'name': 'peak', 'rate': .3, 'hours': [17, 18]}], 'default_rate': .1}])
    b.save_tariff(p)
    b.update({'pack': p['id']})
    amount, _ = b.price_today(10, [1 if h == hour else 0 for h in range(24)], day)
    assert amount == 3


def test_review_3_recent_history_uses_wall_hours_on_dst_day(tmp_path, monkeypatch):
    monkeypatch.setenv('SENSELESS_TZ', 'America/Chicago')
    class History:
        def get(self, scale, key):
            return {'kwh': [1 if h == 17 else 0 for h in range(24)]}
    b = Billing(tmp_path / 's.db', History())
    for row in b._recent_hourly(date(2026, 3, 9)):
        if row['kwh']:
            assert datetime.fromtimestamp(row['ts'], ZoneInfo('America/Chicago')).hour == 17


@pytest.mark.parametrize('zone', ['Asia/Kolkata', 'Asia/Kathmandu'])
def test_review_4_fractional_offset_keeps_tou_boundary(tmp_path, monkeypatch, zone):
    monkeypatch.setenv('SENSELESS_TZ', zone)
    b = Billing(tmp_path / 's.db')
    p = pack([{'type': 'tou', 'periods': [{'name': 'peak', 'rate': .3, 'hours': [17, 18]}], 'default_rate': .1}])
    p['timezone'] = zone
    b.save_tariff(p)
    b.update({'pack': p['id']})
    tz = ZoneInfo(zone)
    at = lambda hour, minute: int(datetime(2026, 1, 1, hour, minute, tzinfo=tz).timestamp())
    b.usage.save([(at(16, 45), 900, 1., None, None), (at(17, 0), 900, 1., None, None)], 'synthetic')
    rows = b._meter_rows()
    assert sum(r['kwh'] for r in rows) == 2
    assert sum(r['coverage_seconds'] for r in rows) == 1800
    assert b.estimate(2, hourly=rows)['total'] == .4
    # A single observation straddling the boundary also needs splitting.
    b2 = Billing(tmp_path / 'crossing.db')
    b2.save_tariff(p)
    b2.update({'pack': p['id']})
    b2.usage.save([(at(16, 45), 1800, 2., None, None)], 'synthetic')
    assert b2.estimate(2, hourly=b2._meter_rows())['total'] == .4


@pytest.mark.parametrize('offset', [0, 60])
@pytest.mark.parametrize('opaque_ids', [False, True], ids=['resource-paths', 'opaque-atom-ids'])
def test_review_5_resolve_each_usage_point_before_aggregation(tmp_path, offset, opaque_ids):
    # Meter A: .25 kWh quarter-hour correction within a 1 kWh hour.
    # Meter B: 2.5 kWh hour. A's correction must not suppress B's hour.
    def block(meter, seconds, value, offset=0):
        return f'''<entry><id>UsagePoint/{meter}/MeterReading/1/IntervalBlock/{seconds}</id>
          <link rel="related" href="ReadingType/1"/><content><e:IntervalBlock>
          <e:IntervalReading><e:timePeriod><e:start>{1735707600 + offset}</e:start><e:duration>{seconds}</e:duration>
          </e:timePeriod><e:value>{value}</e:value></e:IntervalReading></e:IntervalBlock></content></entry>'''
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:e="http://naesb.org/espi">
      <entry><id>ReadingType/1</id><content><e:ReadingType><e:uom>72</e:uom></e:ReadingType></content></entry>'''
    xml += block(PII[4], 3600, 1000) + block(PII[4], 900, 250, offset) + block('OTHER-FAKE', 3600, 2500) + '</feed>'
    if opaque_ids:
        # Atom resource ids need not contain recognizable REST paths.
        for meter in (PII[4], 'OTHER-FAKE'):
            xml = xml.replace(f'UsagePoint/{meter}/MeterReading/1/IntervalBlock/', f'urn:block:{meter}:')
            for seconds in (900, 3600):
                identity = f'<id>urn:block:{meter}:{seconds}</id>'
                xml = xml.replace(identity, identity + f'<link rel="up" href="urn:reading:{meter}"/>')
            parents = f'<entry><id>urn:point:{meter}</id><content><e:UsagePoint/></content></entry>'
            parents += f'<entry><id>urn:reading:{meter}</id><link rel="up" href="urn:point:{meter}"/></entry>'
            xml = xml.replace('</feed>', parents + '</feed>')
    b = Billing(tmp_path / 's.db')
    result = b.import_data(xml.encode())
    assert sum(r['kwh'] for r in b._meter_rows()) == pytest.approx(3.5)
    assert sum(r['coverage_seconds'] for r in b._meter_rows()) == 3600
    assert sum(r[2] for r in import_data(xml.encode())['rows']) == pytest.approx(3.5)
    assert b.import_data(xml.encode())['updated'] == result['added']
    assert sum(r['kwh'] for r in Billing(b.db_path)._meter_rows()) == pytest.approx(3.5)
    private(b, result, b.usage.intervals())


@pytest.mark.parametrize('reverse', [False, True])
def test_review_6_smt_newest_revision_wins_per_flow(tmp_path, reverse):
    rows = [[PII[2], '01/02/2026', revision, '23:45', '24:00', kwh, 'Actual', flow]
            for revision, kwh, flow in [('01/04/2026', '2', 'Consumption'),
                                        ('01/03/2026', '1', 'Consumption'),
                                        ('01/05/2026', '.6', 'Surplus Generation'),
                                        ('01/02/2026', '.1', 'Surplus Generation')]]
    b = Billing(tmp_path / 's.db')
    b.import_data(csv_data(csv_generic.SMT, rows[::-1] if reverse else rows))
    interval, = b.usage.intervals()
    assert (interval['kwh'], interval['received']) == (2., .6)
    private(b, b.usage.intervals())


def test_review_7_peak_exports_keep_annual_and_cycle_comparisons(tmp_path, monkeypatch):
    monkeypatch.setenv('SENSELESS_TZ', 'America/New_York')
    now = datetime(2026, 10, 16, 12, tzinfo=ZoneInfo('America/New_York')).timestamp()
    b = Billing(tmp_path / 's.db', DailyHistory(), clock=lambda: now)
    b.update({'plan': 'a-tou'})
    put_hours(b, date(2025, 10, 1), date(2026, 10, 1),
              usage=lambda dt: -1 if dt.weekday() < 5 and 17 <= dt.hour < 21 else 3)
    year = b.insights()['plans_12mo']
    assert year is not None
    for month in year['months']:
        assert month['kwh'] > 0
        assert 0 <= month['on_peak_share'] <= 1
        assert all(total >= 0 for total in month['totals'].values())
    assert b.cycle()['compare_basis'] == '12 months of meter data'


@pytest.mark.parametrize('component,total,tax', [
    ({'type': 'minimum', 'amount': 10}, 11., 1.),
    ({'type': 'credit', 'amount': -10, 'min_kwh': 0}, -11., -1.),
])
def test_review_8_zero_usage_taxes_monetary_adjustments(component, total, tax):
    p = pack([component], taxes=[{'label': 'Tax', 'rate': .1, 'exempt_kwh': 0,
                                 'fixed_taxed': False, 'applies_to': [component['type']]}])
    assert price(p, 0)['total'] == total
    assert price(p, 0)['tax'] == tax
    assert price(p, 1)['total'] == total
    p['taxes'][0]['exempt_kwh'] = 10
    assert price(p, 0)['tax'] == 0
    assert price(p, 5)['tax'] == 0
    assert price(p, 20)['tax'] == tax / 2
