"""Provider import/storage regressions: all household values below are fake sentinels."""
import csv
import io
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from senseless.billing import Billing, BillingError
from senseless.energy.importers import IMPORTERS, detect, import_data
from senseless.energy.importers import csv_generic, greenbutton_xml

PII = ('Pat Example', '1 Test Rd', 'FAKE-ESIID', 'FAKE-ACCOUNT', 'FAKE-METER')


def csv_data(header, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode()


def private(billing, *outputs):
    with billing._conn() as db:
        persisted = '\n'.join(db.iterdump())
    for value in PII:
        assert value not in persisted
        for output in outputs:
            assert value not in json.dumps(output)


def xml_data():
    # Two UsagePoints, two ReadingTypes and three IntervalBlocks. ReadingType links
    # deliberately aren't adjacent to their blocks; don't take the first type globally.
    return b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:e="http://naesb.org/espi">
    <entry><id>ReadingType/1</id><content><e:ReadingType><e:uom>72</e:uom>
      <e:powerOfTenMultiplier>1</e:powerOfTenMultiplier><e:flowDirection>1</e:flowDirection></e:ReadingType></content></entry>
    <entry><id>ReadingType/2</id><content><e:ReadingType><e:uom>72</e:uom>
      <e:powerOfTenMultiplier>-1</e:powerOfTenMultiplier><e:flowDirection>19</e:flowDirection></e:ReadingType></content></entry>
    <entry><id>UsagePoint/FAKE-METER/MeterReading/1</id><link rel="related" href="ReadingType/1"/></entry>
    <entry><id>UsagePoint/FAKE-METER/MeterReading/2</id><link rel="related" href="ReadingType/2"/></entry>
    <entry><id>UsagePoint/OTHER-FAKE/MeterReading/3</id><link rel="related" href="ReadingType/1"/></entry>
    <entry><link rel="up" href="UsagePoint/FAKE-METER/MeterReading/1"/><content><e:IntervalBlock>
      <e:IntervalReading><e:timePeriod><e:start>1735707600</e:start><e:duration>900</e:duration></e:timePeriod><e:value>100</e:value></e:IntervalReading>
      <e:IntervalReading><e:timePeriod><e:start>1735708500</e:start><e:duration>900</e:duration></e:timePeriod><e:value>200</e:value></e:IntervalReading>
    </e:IntervalBlock></content></entry>
    <entry><link rel="up" href="UsagePoint/FAKE-METER/MeterReading/2"/><content><e:IntervalBlock>
      <e:IntervalReading><e:timePeriod><e:start>1735707600</e:start><e:duration>900</e:duration></e:timePeriod><e:value>5000</e:value></e:IntervalReading>
    </e:IntervalBlock></content></entry>
    <entry><link rel="up" href="UsagePoint/OTHER-FAKE/MeterReading/3"/><content><e:IntervalBlock>
      <e:IntervalReading><e:timePeriod><e:start>1735707600</e:start><e:duration>900</e:duration></e:timePeriod><e:value>50</e:value></e:IntervalReading>
    </e:IntervalBlock></content></entry></feed>'''


def test_xml_links_multipliers_multiple_blocks_and_received(tmp_path):
    b = Billing(tmp_path / 's.db')
    module, confidence = detect(xml_data())
    assert module is greenbutton_xml and confidence > .5
    result = import_data(xml_data())
    assert result['source'] == module.NAME
    assert result['rows'] == [(1735707600, 900, 1.5, None, .5), (1735708500, 900, 2., None, None)]
    preview = b.preview_import(xml_data())
    assert preview['kind'] == 'usage' and len(preview['sample']) == 2
    assert b.usage.intervals() == []
    saved = b.import_data(xml_data())
    assert (saved['added'], saved['importer']) == (2, 'greenbutton-xml')
    assert b._meter_rows()[0]['kwh'] == 3.5
    assert b._meter_rows()[0]['coverage_seconds'] == 1800
    assert b.import_data(xml_data())['updated'] == 2
    private(b, preview, saved, b.usage.intervals())


@pytest.mark.parametrize('transform', [lambda d: d.replace(b'<e:uom>72', b'<e:uom>99'),
    lambda d: d.replace(b'<e:value>100', b'<e:value>nan'),
    lambda d: d.replace(b'<e:duration>900', b'<e:duration>299'),
    lambda d: b'<!DOCTYPE feed [<!ENTITY p "Pat Example">]>' + d,
    lambda d: d[:-5]])
def test_xml_errors_are_private_and_atomic(tmp_path, transform, caplog):
    b = Billing(tmp_path / 's.db')
    with pytest.raises(BillingError) as error:
        b.import_data(transform(xml_data()))
    assert b.usage.intervals() == []
    private(b, str(error.value), caplog.text)


def test_csv_profiles_trim_case_units_and_received(tmp_path, monkeypatch):
    monkeypatch.setenv('SENSELESS_TZ', 'America/New_York')
    b = Billing(tmp_path / 's.db')
    smt = csv_data([' ' + h.lower() + ' ' for h in csv_generic.SMT], [
        [PII[2], '01/02/2026', '01/03/2026', '00:00', '00:15', '1.5', 'Actual', 'Consumption'],
        [PII[2], '01/02/2026', '01/03/2026', '00:00', '00:15', '.4', 'Actual', 'Surplus Generation'],
        [PII[2], '01/02/2026', '01/03/2026', '00:15', '00:30', '2', 'Actual', 'Consumption']])
    result = b.import_data(smt)
    rows = b.usage.intervals()
    assert result['importer'] == 'csv-generic' and result['label'] == 'Smart Meter Texas CSV'
    assert rows[0]['ts'] == int(datetime(2026, 1, 2, tzinfo=ZoneInfo('America/Chicago')).timestamp())
    assert (rows[0]['seconds'], rows[0]['kwh'], rows[0]['received']) == (900, 1.5, .4)
    green = csv_data(csv_generic.GREENBUTTON, [
        ['Electric usage', '01/03/2026', '00:00', '01:00', '1500', 'Wh', '', PII[0]],
        ['Electric usage', '01/03/2026', '23:00', '00:00', '2', 'kWh', '', PII[1]]])
    preview = b.preview_import(green)
    assert preview['sample'][0][2] == 1.5
    assert preview['sample'][1][1] == 3600
    b.import_data(green)
    private(b, result, rows, preview, b.insights())


def test_saved_mapping_roundtrip_preview_and_unknown_header_privacy(tmp_path):
    b = Billing(tmp_path / 's.db')
    header = ['Customer Name', 'Account Number', 'ESIID', 'Meter', 'Service', 'Address', 'Pat Example', 'When', 'Until', 'Energy', 'Temperature']
    data = csv_data(header, [[PII[0], PII[3], PII[2], PII[4], 'Fake Service', PII[1], PII[0],
                             '2026-01-01T00:00:00+00:00', '2026-01-01T00:15:00+00:00', '1500', '35']])
    preview = b.preview_import(data)
    assert preview['importer'] is None and preview['sample'] == []
    assert preview['header'][:7] == ['[redacted]'] * 7
    with pytest.raises(BillingError) as error:
        b.import_data(data)
    private(b, preview, str(error.value))
    # Column *names* in a mapping must not contain household values either.
    header.remove('Pat Example')
    data = csv_data(header, [[PII[0], PII[3], PII[2], PII[4], 'Fake Service', PII[1],
                            '2026-01-01T00:00:00+00:00', '2026-01-01T00:15:00+00:00', '1500', '35']])
    profile = {'name': 'my-export', 'match_headers': header,
               'columns': {'start': 'When', 'end': 'Until', 'kwh': 'Energy', 'temp_f': 'Temperature'}, 'units': 'Wh'}
    b.save_csv_profile(profile)
    b = Billing(b.db_path)
    preview = b.preview_import(data)
    assert preview['sample'][0][1:] == (900, 1.5, 35., None)
    assert b.usage.intervals() == []
    result = b.import_data(data)
    assert result['label'] == 'Saved CSV mapping'
    assert b.usage.intervals()[0]['kwh'] == 1.5
    private(b, preview, result, b.csv_profiles())
    b.remove_csv_profile('my-export')
    with pytest.raises(KeyError):
        b.remove_csv_profile('my-export')


@pytest.mark.parametrize('columns', [{'start': 'When', 'kwh': 'ESIID'}, {'start': 'Customer Name', 'kwh': 'Energy'},
                                    {'start': 'When', 'kwh': 'Energy', 'name': 'Customer Name'}])
def test_mappings_cannot_select_identity_columns(tmp_path, columns):
    b = Billing(tmp_path / 's.db')
    with pytest.raises(BillingError):
        b.save_csv_profile({'name': 'bad', 'match_headers': ['When', 'ESIID', 'Customer Name', 'Energy'], 'columns': columns})
    assert b.csv_profiles() == []


def test_mapping_date_time_format_timezone_and_dst_rejection(tmp_path):
    b = Billing(tmp_path / 's.db')
    b.save_csv_profile({'name': 'split-time', 'match_headers': ['Day', 'Clock', 'Energy'],
                        'columns': {'date': 'Day', 'time': 'Clock', 'kwh': 'Energy'},
                        'time_format': '%m/%d/%Y %H:%M', 'timezone': 'America/New_York', 'units': 'kWh'})
    data = csv_data(['Day', 'Clock', 'Energy'], [['01/02/2026', '13:00', '2']])
    assert b.preview_import(data)['sample'][0][0] == int(datetime(2026, 1, 2, 13, tzinfo=ZoneInfo('America/New_York')).timestamp())
    for day, clock in [('11/01/2026', '01:30'), ('03/08/2026', '02:30')]:
        with pytest.raises(BillingError):
            b.import_data(csv_data(['Day', 'Clock', 'Energy'], [[day, clock, '2']]))
    assert b.usage.intervals() == []


def test_migration_and_finer_interval_overlap(tmp_path):
    path = tmp_path / 's.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE meter_hourly (ts INTEGER PRIMARY KEY, kwh REAL, temp_f REAL, delivered REAL, received REAL)')
        db.execute('INSERT INTO meter_hourly VALUES (0, 4, 30, 4, .4)')
        db.execute('CREATE TABLE billing (id INTEGER PRIMARY KEY, body TEXT)')
        db.execute('INSERT INTO billing VALUES (1, ?)', (json.dumps({'plan': 'cmp-a', 'custom': {'fixed': 10, 'delivery': .2}}),))
    b = Billing(path)
    assert b.settings()['pack'] == 'us-me-cmp' and b.settings()['plan'] == 'rate-a'
    assert b.settings()['supply'] == 'standard-offer-2026'
    assert b.usage.intervals()[0]['source'] == 'avangrid-csv'
    assert Billing(path).usage.intervals() == b.usage.intervals()
    with b._conn() as db:
        custom = json.loads(db.execute('SELECT body FROM user_tariffs WHERE id="custom"').fetchone()[0])
    assert custom['components'][0]['amount'] == 10 and custom['components'][1]['rate'] == .2
    b.usage.save([(0, 900, .1, 40., .01), (900, 900, .2, 40., .02)], 'synthetic')
    # Half the old hour remains: .1 + .2 + 4 * .5 = 2.3, not 4.3 or .3.
    hour = b._meter_rows()[0]
    assert hour['kwh'] == pytest.approx(2.3) and hour['received'] == pytest.approx(.23)
    assert hour['temp_f'] == 35 and hour['coverage_seconds'] == 3600
    b.usage.save([(0, 3600, 8., 30., .8)], 'corrected')
    assert Billing(path)._meter_rows()[0]['kwh'] == pytest.approx(4.3)
    b.usage.save([(1800, 900, .3, None, None), (2700, 900, .4, None, None)], 'synthetic')
    assert b._meter_rows()[0]['kwh'] == pytest.approx(1.)
    # A 30-minute observation crossing the hour is split without losing energy.
    b.usage.save([(6300, 1800, 2., None, None)], 'synthetic')
    assert b._meter_rows()[1]['kwh'] == b._meter_rows()[2]['kwh'] == 1.


@pytest.mark.parametrize('old,new', [('cmp-a', 'rate-a'), ('cmp-a-tou', 'a-tou'), ('cmp-et', 'electric-technology'), ('custom', 'custom')])
def test_all_legacy_settings_migrate_idempotently(tmp_path, old, new):
    path = tmp_path / 's.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE billing (id INTEGER PRIMARY KEY, body TEXT)')
        db.execute('INSERT INTO billing VALUES (1, ?)', (json.dumps({'plan': old, 'supply_per_kwh': .2}),))
    b = Billing(path)
    s = b.settings()
    assert s['plan'] == new and s['supply_per_kwh'] == .2
    assert Billing(path).settings() == s
    assert b.update({'plan': old})['plan'] == new


def test_registry_modules_have_public_contract():
    for module in IMPORTERS:
        assert module.NAME and module.LABEL
        assert 0 <= module.detect(b'unknown') <= 1


def test_smt_midnight_and_revised_rows(tmp_path):
    b = Billing(tmp_path / 's.db')
    data = csv_data(csv_generic.SMT, [
        ['FAKE-ESIID', '01/02/2026', '01/03/2026', '23:45', '24:00', '1', 'Actual', 'Consumption'],
        ['FAKE-ESIID', '01/02/2026', '01/04/2026', '23:45', '24:00', '2', 'Actual', 'Consumption'],
        ['FAKE-ESIID', '01/02/2026', '01/04/2026', '23:45', '24:00', '.5', 'Actual', 'Surplus Generation']])
    b.import_data(data)
    row = b.usage.intervals()[0]
    assert (row['seconds'], row['kwh'], row['received']) == (900, 2, .5)
    private(b, b.usage.intervals())


def test_legacy_cmp_bill_lines_migrate_without_losing_amount(tmp_path):
    from test_billing_import import SINGLE
    from senseless.energy.importers.bill_cmp import parse_cmp_bill_text
    b = Billing(tmp_path / 's.db')
    bill = b._save_bill(parse_cmp_bill_text(SINGLE))
    assert sum(line['amount'] for line in bill['lines']) == pytest.approx(bill['amount'])
    with b._conn() as db:
        db.execute("UPDATE bills SET source='bill-pdf', lines_json=NULL")
    reopened = Billing(b.db_path).bills()[0]
    assert reopened['source'] == 'bill-cmp' and reopened['amount'] == bill['amount']
    assert sum(line['amount'] for line in reopened['lines']) == pytest.approx(bill['amount'])
    assert {line['kind'] for line in reopened['lines']} == {'fixed', 'delivery', 'supply', 'tax'}
    assert Billing(b.db_path).bills()[0] == reopened
