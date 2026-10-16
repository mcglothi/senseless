"""Synthetic data-only tariff arithmetic, validation, persistence and comparison."""
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from senseless.billing import Billing, BillingError
from senseless.energy.tariff import builtin_packs, estimate, validate_pack


def pack(components, *, taxes=None, supply=None, kind='bundled'):
    return {'id': 'test-utility', 'utility': 'Example Utility', 'region': 'US-TX', 'currency': 'USD',
            'timezone': 'America/Chicago', 'source': 'synthetic test tariff',
            'plans': [{'id': 'test-plan', 'label': 'Test plan', 'kind': kind, 'effective': '2026-01-01',
                       'source': 'synthetic', 'components': components}], 'supply': supply or [], 'taxes': taxes or []}


def price(value, kwh, **kwargs):
    return estimate(validate_pack(value), 'test-plan', kwargs.pop('supply_id', None), kwh, **kwargs)


def test_cmp_pack_reproduces_existing_estimates_to_cent():
    cmp = builtin_packs()['us-me-cmp']
    assert estimate(cmp, 'rate-a', 'standard-offer-2026', 550)['total'] == 157.13
    assert estimate(cmp, 'rate-a', 'standard-offer-2026', 1350)['total'] == 362.71
    et = next(p for p in cmp['plans'] if p['id'] == 'electric-technology')
    assert et['expires'] == '2027-01-31' and et['source']
    assert estimate(cmp, 'cmp-a', 'standard-offer-2026', 550)['total'] == 157.13


def test_tiers_fixed_allowance_rider_and_daily_proration():
    p = pack([{'type': 'fixed', 'per': 'day', 'amount': 1, 'includes_kwh': 1},
              {'type': 'tiered', 'tiers': [{'up_to_kwh': 500, 'rate': .1}, {'rate': .14}]},
              {'type': 'rider', 'rate': .003}])
    e = price(p, 1000, days=30)
    assert e['fixed'] == 30 and e['delivery'] == pytest.approx(50 + 470 * .14 + 3)
    assert e['total'] == 148.8 and e['marginal_cents_per_kwh'] == 14.3
    assert len(e['lines']) == 3
    monthly = pack([{'type': 'fixed', 'per': 'month', 'amount': 20, 'includes_kwh': 50}, {'type': 'energy', 'rate': .1}])
    assert price(monthly, 75, months=.5)['total'] == 15


def test_texas_bundled_usage_credit_and_minimum():
    p = pack([{'type': 'minimum', 'amount': 10}, {'type': 'fixed', 'per': 'month', 'amount': 5},
              {'type': 'energy', 'rate': .15}, {'type': 'rider', 'rate': .05},
              {'type': 'credit', 'min_kwh': 1000, 'max_kwh': 2000, 'amount': -100}])
    assert price(p, 0)['total'] == 10
    assert price(p, 999)['total'] == 204.8
    assert price(p, 1000)['total'] == 105
    assert price(p, 2000)['total'] == 305
    assert price(p, 2001)['total'] == 405.2
    # Crossing a credit threshold legitimately has a negative marginal price.
    assert price(p, 999)['marginal_cents_per_kwh'] == -9980
    assert price(p, 1000)['supply'] == 0  # bundled supply must never be charged twice


def test_multiple_tou_periods_months_weekends_and_holidays():
    periods = [{'name': 'summer peak', 'rate': .4, 'hours': [17, 21], 'weekdays': [0, 1, 2, 3, 4], 'months': [7, 8], 'holidays': False},
               {'name': 'overnight', 'rate': .05, 'hours': [0, 6], 'holidays': True}]
    p = pack([{'type': 'tou', 'periods': periods, 'default_rate': .1}])
    tz = ZoneInfo(p['timezone'])
    profile = [{'ts': int(datetime.fromisoformat(d).replace(tzinfo=tz).timestamp()), 'kwh': 1., 'holiday': holiday}
               for d, holiday in [('2026-07-06T18:00', False), ('2026-07-06T18:00', True),
                                  ('2026-07-04T18:00', False), ('2026-01-05T18:00', False),
                                  ('2026-07-06T02:00', True)]]
    assert price(p, 5, hourly=profile)['total'] == .75
    with pytest.raises(BillingError):
        price(p, 5)
    with pytest.raises(BillingError):
        price(p, 5, hourly=profile[:1])
    with pytest.raises(BillingError):
        price(p, 1, hourly=[{'ts': 10 ** 30, 'kwh': 1}])
    assert price(p, 0, hourly=[])['total'] == 0
    with pytest.raises(BillingError):
        price(p, float('nan'))


def test_tax_exemption_and_fixed_taxation():
    tax = {'rate': .1, 'exempt_kwh': 100, 'applies_to': ['energy', 'rider'], 'fixed_taxed': False, 'label': 'Synthetic sales tax'}
    p = pack([{'type': 'fixed', 'per': 'month', 'amount': 20, 'includes_kwh': 50},
              {'type': 'energy', 'rate': .1}, {'type': 'rider', 'rate': .01}], taxes=[tax])
    assert price(p, 100)['tax'] == 0
    assert price(p, 200)['tax'] == 1.1
    assert price(p, 200)['total'] == 38.1
    assert price(p, 200)['marginal_cents_per_kwh'] == 12.1
    p['taxes'][0]['fixed_taxed'] = True
    assert price(p, 200)['tax'] == 3.1
    p['taxes'][0].update(fixed_taxed=False, exempt_kwh=0)
    assert price(p, 60)['tax'] == .16  # 10 charged energy kWh plus the 60-kWh rider
    tiered = pack([{'type': 'tiered', 'tiers': [{'up_to_kwh': 100, 'rate': .1}, {'rate': .2}]}],
                  taxes=[dict(tax, exempt_kwh=100, applies_to=['tiered'])])
    assert price(tiered, 200)['tax'] == 2


def test_delivery_plus_supply():
    supply = [{'id': 'test-supply', 'label': 'Example supply', 'kind': 'supply', 'source': 'synthetic', 'effective': '2026-01-01',
               'components': [{'type': 'energy', 'rate': .2}]}]
    p = pack([{'type': 'fixed', 'per': 'month', 'amount': 10}, {'type': 'energy', 'rate': .1}], supply=supply, kind='delivery')
    e = price(p, 100, supply_id='test-supply')
    assert (e['fixed'], e['delivery'], e['supply'], e['total']) == (10, 10, 20, 40)
    assert e['marginal_cents_per_kwh'] == 30


@pytest.mark.parametrize('component', [
    {'type': 'python', 'code': 'anything'}, {'type': 'energy', 'rate': -.1}, {'type': 'energy', 'rate': float('nan')},
    {'type': 'rider', 'rate': True}, {'type': 'fixed', 'per': 'year', 'amount': 1}, {'type': 'fixed', 'per': 'month', 'amount': -1},
    {'type': 'credit', 'min_kwh': 1000, 'max_kwh': 500, 'amount': -10}, {'type': 'credit', 'min_kwh': 0, 'amount': 10},
    {'type': 'minimum', 'amount': -10}, {'type': 'tiered', 'tiers': [{'up_to_kwh': 100, 'rate': .1}]},
    {'type': 'tiered', 'tiers': [{'up_to_kwh': 100, 'rate': .1}, {'up_to_kwh': 50, 'rate': .2}, {'rate': .3}]},
    {'type': 'tou', 'periods': [{'name': 'bad', 'rate': .1, 'hours': [21, 17]}], 'default_rate': .1},
    {'type': 'tou', 'periods': [{'name': 'bad', 'rate': .1, 'hours': [0, 25]}], 'default_rate': .1},
    {'type': 'tou', 'periods': [{'name': 'bad', 'rate': .1, 'hours': [0, 24], 'months': [13]}], 'default_rate': .1},
])
def test_bad_components_rejected(component):
    with pytest.raises(BillingError):
        validate_pack(pack([component]))


@pytest.mark.parametrize('patch', [{'timezone': 'not/a-zone'}, {'id': '../escape'}, {'taxes': [{'rate': -.1}]},
                                  {'plans': []}, {'supply': None}, {'unknown': 'Pat Example'}])
def test_bad_pack_rejected(patch):
    p = pack([{'type': 'energy', 'rate': .1}]) | patch
    with pytest.raises(BillingError) as error:
        validate_pack(p)
    assert 'Pat Example' not in str(error.value)


def test_user_pack_crud_selection_and_date_limited_bill_check(tmp_path):
    b = Billing(tmp_path / 's.db')
    p = pack([{'type': 'energy', 'rate': .2}])
    b.save_tariff(p)
    assert b.tariffs.get(p['id']) == p
    s = b.update({'pack': p['id']})
    assert (s['plan'], s['supply']) == ('test-plan', None)
    assert b.estimate(100)['total'] == 20
    assert b.rates()['marginal_cents_per_kwh'] == 20
    row = b.add_bill({'start': '2026-09-01', 'end': '2026-10-01', 'kwh': 100, 'amount': 21, 'name': 'Pat Example'})
    assert row['estimate'] == 20
    assert b.insights()['bills'][0]['check'] == {'model_total': 20, 'diff': 1}
    b.add_bill({'start': '2025-09-01', 'end': '2025-10-01', 'kwh': 100, 'amount': 21})
    assert b.insights()['bills'][1]['check'] is None
    assert Billing(b.db_path).settings() == s
    invalid_replacement = copy.deepcopy(p)
    invalid_replacement['plans'][0]['id'] = 'replacement'
    with pytest.raises(BillingError):
        b.save_tariff(invalid_replacement)
    assert b.tariffs.get(p['id']) == p
    with pytest.raises(BillingError):
        b.remove_tariff(p['id'])
    with pytest.raises(BillingError):
        b.save_tariff(b.tariffs.get('us-me-cmp'))
    with pytest.raises(BillingError):
        b.remove_tariff('us-me-cmp')
    b.update({'pack': 'us-me-cmp'})
    b.remove_tariff(p['id'])
    with pytest.raises(KeyError):
        b.remove_tariff(p['id'])
    with b._conn() as db:
        assert 'Pat Example' not in '\n'.join(db.iterdump())


def test_generic_cycle_and_annual_compares_selected_pack(tmp_path):
    from test_billing_import import DailyHistory, put_hours
    from datetime import date
    clock = datetime(2026, 10, 16, 12, tzinfo=ZoneInfo('America/New_York')).timestamp()
    b = Billing(tmp_path / 's.db', DailyHistory(), clock=lambda: clock)
    p = pack([{'type': 'energy', 'rate': .2}])
    p['plans'][0]['components'].append({'type': 'fixed', 'per': 'day', 'amount': 1})
    p['plans'].append({'id': 'night-plan', 'label': 'Night plan', 'kind': 'bundled', 'effective': '2026-01-01', 'source': 'synthetic',
                       'components': [{'type': 'tou', 'periods': [
                           {'name': 'evening', 'rate': .3, 'hours': [17, 21]},
                           {'name': 'daytime', 'rate': .2, 'hours': [6, 17]}], 'default_rate': .1}]})
    b.save_tariff(p)
    b.update({'pack': p['id'], 'plan': 'night-plan'})
    c = b.cycle()
    assert {r['plan'] for r in c['compare']} == {'test-plan', 'night-plan'}
    assert c['projected']['total'] > 0 and b.rates()['marginal_cents_per_kwh'] > 0
    put_hours(b, date(2025, 10, 1), date(2026, 10, 1))
    year = b.insights()['plans_12mo']
    assert set(year['totals']) == {'test-plan', 'night-plan'}
    for month in year['months']:
        import calendar
        year_number, month_number = map(int, month['month'].split('-'))
        assert month['totals']['test-plan'] == round(month['kwh'] * .2 + calendar.monthrange(year_number, month_number)[1], 2)
    assert b.cycle()['compare_basis'] == '12 months of meter data'


def test_credit_can_create_negative_bill_without_an_explicit_minimum():
    p = pack([{'type': 'credit', 'min_kwh': 0, 'max_kwh': None, 'amount': -10}])
    assert price(p, 0)['total'] == -10


def test_tou_fixed_allowance_and_supply_tou_in_billing(tmp_path):
    p = pack([{'type': 'fixed', 'per': 'month', 'amount': 10, 'includes_kwh': 50},
              {'type': 'tou', 'periods': [{'name': 'peak', 'rate': .2, 'hours': [17, 21]}], 'default_rate': .1}])
    assert price(p, 100, on_peak_kwh=20)['total'] == 16
    supply = [{'id': 'tou-supply', 'label': 'Example TOU supply', 'kind': 'supply', 'effective': '2026-01-01', 'source': 'synthetic',
               'components': [{'type': 'tou', 'periods': [{'name': 'peak', 'rate': .3, 'hours': [17, 21]}], 'default_rate': .1}]}]
    p = pack([{'type': 'energy', 'rate': .05}], kind='delivery', supply=supply)
    b = Billing(tmp_path / 's.db')
    b.save_tariff(p)
    b.update({'pack': p['id']})
    assert b.plan()['tou'] is not None
    assert b.estimate(100, 20)['total'] == 19
    assert b.rates()['marginal_on_cents'] == 35


def test_live_cost_uses_each_tou_period_in_pack_timezone(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setenv('SENSELESS_TZ', 'America/Chicago')
    b = Billing(tmp_path / 's.db')
    p = pack([{'type': 'tou', 'periods': [{'name': 'peak', 'rate': .3, 'hours': [17, 21]},
                                         {'name': 'day', 'rate': .2, 'hours': [6, 17]}], 'default_rate': .1}])
    b.save_tariff(p)
    b.update({'pack': p['id']})
    for hour, expected in [(2, 1.), (10, 2.), (18, 3.)]:
        weights = [1. if h == hour else 0. for h in range(24)]
        amount, _ = b.price_today(10., weights, date(2026, 10, 15))
        assert amount == expected


def test_generic_calibration_requires_matching_snapshot_and_effective_dates(tmp_path):
    p = pack([{'type': 'energy', 'rate': .2}])
    b = Billing(tmp_path / 's.db')
    b.save_tariff(p)
    b.update({'pack': p['id']})
    assert b.calibration() is None
    b.add_bill({'start': '2025-09-01', 'end': '2025-10-01', 'kwh': 100, 'amount': 40})
    assert b.calibration() is None
    bill = b.add_bill({'start': '2026-09-01', 'end': '2026-10-01', 'kwh': 100, 'amount': 22})
    assert json.loads(bill['tariff_json'])['plan']['components'] == p['plans'][0]['components']
    assert b.calibration() == 1.1
    updated = copy.deepcopy(p)
    updated['plans'][0]['components'][0]['rate'] = .3
    b.save_tariff(updated)
    assert b.calibration() is None
    b.save_tariff(p)
    assert b.calibration() == 1.1
    b.update({'pack': 'us-me-cmp'})
    assert b.calibration() is None


def test_daily_fixed_charge_uses_actual_bill_days(tmp_path):
    b = Billing(tmp_path / 's.db')
    p = pack([{'type': 'fixed', 'per': 'day', 'amount': 1}, {'type': 'energy', 'rate': .1}])
    b.save_tariff(p)
    b.update({'pack': p['id']})
    bill = b.add_bill({'start': '2026-08-29', 'end': '2026-09-30', 'kwh': 100, 'amount': 42})
    assert bill['estimate'] == 42
    assert b.insights()['bills'][0]['check'] == {'model_total': 42, 'diff': 0}
    assert b.calibration() == 1
