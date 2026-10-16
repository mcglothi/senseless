"""Validated data-only tariff packs and one bill engine. No external plugins or code loading."""
import copy
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import BillingError

PACKS = Path(__file__).with_name('packs')
ALIASES = {'cmp-a': 'rate-a', 'cmp-a-tou': 'a-tou', 'cmp-et': 'electric-technology'}
COMPONENTS = {'fixed', 'energy', 'tiered', 'tou', 'credit', 'minimum', 'rider'}


def number(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError


def validate_pack(pack):
    """Return an independent validated object; all errors are constant, private messages."""
    try:
        if not isinstance(pack, dict) or set(pack) - {'id', 'utility', 'region', 'currency', 'timezone', 'source', 'plans', 'supply', 'taxes'}:
            raise ValueError
        for key in ('id', 'utility', 'region', 'currency', 'timezone', 'source'):
            _text(pack[key])
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', pack['id']):
            raise ValueError
        ZoneInfo(pack['timezone'])
        if not isinstance(pack['plans'], list) or not pack['plans'] or not isinstance(pack['supply'], list):
            raise ValueError
        for group, kinds in ((pack['plans'], {'delivery', 'bundled'}), (pack['supply'], {'supply'})):
            ids = set()
            for plan in group:
                if not isinstance(plan, dict) or set(plan) - {'id', 'label', 'kind', 'effective', 'expires', 'source', 'notes', 'components'}:
                    raise ValueError
                for key in ('id', 'label', 'source'):
                    _text(plan[key])
                if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', plan['id']) or plan['id'] in ids or plan['kind'] not in kinds:
                    raise ValueError
                ids.add(plan['id'])
                effective = date.fromisoformat(plan['effective'])
                if plan.get('expires') and date.fromisoformat(plan['expires']) < effective:
                    raise ValueError
                if 'notes' in plan:
                    _text(plan['notes'])
                if not isinstance(plan['components'], list) or not plan['components']:
                    raise ValueError
                for c in plan['components']:
                    kind = c['type']
                    allowed = {
                        'fixed': {'type', 'per', 'amount', 'includes_kwh'}, 'energy': {'type', 'rate'},
                        'rider': {'type', 'rate'}, 'minimum': {'type', 'amount'},
                        'tiered': {'type', 'tiers'}, 'tou': {'type', 'periods', 'default_rate'},
                        'credit': {'type', 'min_kwh', 'max_kwh', 'amount'}}
                    if kind not in COMPONENTS or set(c) - allowed[kind]:
                        raise ValueError
                    if kind in ('energy', 'rider'):
                        number(c['rate'])
                    elif kind in ('fixed', 'minimum'):
                        number(c['amount'])
                        if kind == 'fixed':
                            if c['per'] not in ('month', 'day'):
                                raise ValueError
                            number(c.get('includes_kwh', 0))
                    elif kind == 'credit':
                        number(c['min_kwh'])
                        if c.get('max_kwh') is not None and number(c['max_kwh']) < c['min_kwh']:
                            raise ValueError
                        if number(c['amount'], -1e9) > 0:
                            raise ValueError
                    elif kind == 'tiered':
                        if not isinstance(c['tiers'], list) or not c['tiers']:
                            raise ValueError
                        previous = 0
                        for index, tier in enumerate(c['tiers']):
                            if set(tier) - {'rate', 'up_to_kwh'}:
                                raise ValueError
                            number(tier['rate'])
                            limit = tier.get('up_to_kwh')
                            if limit is None:
                                if index != len(c['tiers']) - 1:
                                    raise ValueError
                            elif number(limit) <= previous:
                                raise ValueError
                            else:
                                previous = limit
                        if c['tiers'][-1].get('up_to_kwh') is not None:
                            raise ValueError  # final tier must cover all remaining usage
                    else:
                        number(c['default_rate'])
                        if not isinstance(c['periods'], list) or not c['periods']:
                            raise ValueError
                        for period in c['periods']:
                            if set(period) - {'name', 'rate', 'weekdays', 'hours', 'months', 'holidays'}:
                                raise ValueError
                            _text(period['name'])
                            number(period['rate'])
                            hours = period['hours']
                            if not isinstance(hours, list) or len(hours) != 2 or any(type(h) is not int for h in hours) or not 0 <= hours[0] < hours[1] <= 24:
                                raise ValueError
                            for key, lo, hi in [('weekdays', 0, 6), ('months', 1, 12)]:
                                values = period.get(key, list(range(lo, hi + 1)))
                                if not isinstance(values, list) or not values or any(type(v) is not int or not lo <= v <= hi for v in values):
                                    raise ValueError
                            if 'holidays' in period and type(period['holidays']) is not bool:
                                raise ValueError
        if not isinstance(pack['taxes'], list):
            raise ValueError
        for tax in pack['taxes']:
            if set(tax) - {'rate', 'exempt_kwh', 'applies_to', 'fixed_taxed', 'label'}:
                raise ValueError
            _text(tax['label'])
            number(tax['rate'])
            number(tax['exempt_kwh'])
            if type(tax['fixed_taxed']) is not bool or not isinstance(tax['applies_to'], list) or set(tax['applies_to']) - COMPONENTS:
                raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise BillingError('invalid tariff pack (metadata, dates, components, rates or time periods)') from None
    return copy.deepcopy(pack)


def builtin_packs():
    return {p.stem: validate_pack(json.loads(p.read_text())) for p in sorted(PACKS.glob('*.json'))}


class Tariffs:
    def __init__(self, connect):
        self.connect = connect
        self.builtins = builtin_packs()
        with connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tariff_packs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def get(self, pack_id):
        if pack_id in self.builtins:
            return copy.deepcopy(self.builtins[pack_id])
        with self.connect() as db:
            row = db.execute('SELECT body FROM tariff_packs WHERE id=?', (pack_id,)).fetchone()
        if row is None:
            raise BillingError('unknown tariff pack')
        return json.loads(row['body'])

    def list(self):
        with self.connect() as db:
            packs = list(self.builtins.values()) + [json.loads(r['body']) for r in db.execute('SELECT body FROM tariff_packs ORDER BY id')]
        return [{k: p[k] for k in ('id', 'utility', 'region')} |
                {key: [{k: item[k] for k in ('id', 'label', 'kind', 'effective', 'source')} |
                       ({'expires': item['expires']} if 'expires' in item else {}) for item in p[key]]
                 for key in ('plans', 'supply')} for p in packs]

    def save(self, pack):
        pack = validate_pack(pack)
        if pack['id'] in self.builtins:
            raise BillingError('built-in tariff packs are read-only')
        with self.connect() as db:
            db.execute('INSERT INTO tariff_packs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                       (pack['id'], json.dumps(pack)))
        return pack

    def remove(self, pack_id):
        if pack_id in self.builtins:
            raise BillingError('built-in tariff packs are read-only')
        with self.connect() as db:
            if not db.execute('DELETE FROM tariff_packs WHERE id=?', (pack_id,)).rowcount:
                raise KeyError(pack_id)


def selected_plan(pack, plan_id):
    pid = ALIASES.get(plan_id, plan_id) if pack['id'] == 'us-me-cmp' else plan_id
    for plan in pack['plans']:
        if plan['id'] == pid:
            return plan
    raise BillingError('unknown plan')


def period_matches(dt, period, holiday=False):
    return (dt.weekday() in period.get('weekdays', range(7)) and dt.month in period.get('months', range(1, 13))
            and period['hours'][0] <= dt.hour < period['hours'][1]
            and (not holiday or period.get('holidays', True)))


def tou_rate(component, dt, holiday=False):
    for period in component['periods']:
        if period_matches(dt, period, holiday):
            return period['rate']
    return component['default_rate']


def tier_cost(tiers, kwh, months):
    total = previous = 0.0
    for tier in tiers:
        limit = tier.get('up_to_kwh')
        limit = math.inf if limit is None else limit * months
        total += max(0, min(kwh, limit) - previous) * tier['rate']
        previous = limit
    return total


def _calculate(pack, plan, supply, kwh, profile, on_peak_kwh, months, days):
    lines = []
    groups = [plan] + ([supply] if supply else [])
    tz = ZoneInfo(pack['timezone'])
    for group in groups:
        begin = len(lines)
        allowance = sum(c.get('includes_kwh', 0) * (months if c['per'] == 'month' else days)
                        for c in group['components'] if c['type'] == 'fixed')
        for c in group['components']:
            kind = c['type']
            base_kwh = max(0, kwh - allowance)
            if kind == 'fixed':
                amount = c['amount'] * (months if c['per'] == 'month' else days)
            elif kind in ('energy', 'rider'):
                amount = (base_kwh if kind == 'energy' else kwh) * c['rate']
            elif kind == 'tiered':
                amount = tier_cost(c['tiers'], base_kwh, months)
            elif kind == 'tou':
                if profile is not None:
                    amount = sum(r['kwh'] * tou_rate(c, datetime.fromtimestamp(r['ts'], tz), r.get('holiday', False)) for r in profile)
                elif on_peak_kwh is not None and len(c['periods']) == 1:
                    amount = on_peak_kwh * c['periods'][0]['rate'] + (kwh - on_peak_kwh) * c['default_rate']
                else:
                    raise BillingError('time-of-use needs an hourly profile (or on_peak_kwh for a single period)')
                amount *= base_kwh / kwh if kwh else 0
            elif kind == 'credit':
                amount = c['amount'] * months if c['min_kwh'] * months <= kwh and (c.get('max_kwh') is None or kwh <= c['max_kwh'] * months) else 0
            else:
                continue  # minimum applies after all other components, regardless of ordering
            lines.append({'label': group['label'], 'kind': 'fixed' if kind == 'fixed' else 'credit' if kind == 'credit' else group['kind'] if group['kind'] != 'bundled' else 'energy',
                          'type': kind, 'amount': amount, 'component': c, 'allowance': allowance})
        minimum = max((c['amount'] * months for c in group['components'] if c['type'] == 'minimum'), default=None)
        subtotal = sum(line['amount'] for line in lines[begin:])
        if minimum is not None and minimum > subtotal:
            lines.append({'label': group['label'] + ' minimum', 'kind': 'other', 'type': 'minimum', 'amount': minimum - subtotal})
    taxes = []
    for tax in pack['taxes']:
        taxable_kwh = max(0, kwh - tax['exempt_kwh'] * months)
        base = 0
        for line in lines:
            kind = line['type']
            if kind == 'fixed':
                if tax['fixed_taxed']:
                    base += line['amount']
            elif kind in tax['applies_to']:
                c = line.get('component', {})
                if kind in ('energy', 'rider'):
                    base += (taxable_kwh if kind == 'rider' else min(taxable_kwh, max(0, kwh - line['allowance']))) * c['rate']
                elif kind == 'tou':
                    used = max(0, kwh - line['allowance'])
                    base += line['amount'] * min(taxable_kwh, used) / used if used else 0
                elif kind == 'tiered':
                    used = max(0, kwh - line['allowance'])
                    base += tier_cost(c['tiers'], used, months) - tier_cost(c['tiers'], max(0, used - taxable_kwh), months)
                else:
                    base += line['amount'] * taxable_kwh / kwh if kwh else 0
        taxes.append({'label': tax['label'], 'kind': 'tax', 'type': 'tax', 'amount': base * tax['rate']})
    lines += taxes
    fixed = sum(l['amount'] for l in lines if l['kind'] == 'fixed')
    supply_amount = sum(l['amount'] for l in lines if l['kind'] == 'supply')
    tax_amount = sum(l['amount'] for l in taxes)
    total = sum(l['amount'] for l in lines)
    return {'fixed': fixed, 'delivery': total - fixed - supply_amount - tax_amount, 'supply': supply_amount,
            'tax': tax_amount, 'total': total,
            'lines': [{k: v for k, v in l.items() if k not in ('component', 'allowance')} for l in lines]}


def estimate(pack, plan_id, supply_id, kwh, *, hourly=None, on_peak_kwh=None, months=1.0, days=None, marginal_hourly=None):
    try:
        number(kwh)
        if number(months) <= 0:
            raise ValueError
        days = months * 30.44 if days is None else number(days)
        if on_peak_kwh is not None and not 0 <= number(on_peak_kwh) <= kwh:
            raise ValueError
        for profile, expected in ((hourly, kwh), (marginal_hourly, 1)):
            if profile is not None:
                for row in profile:
                    if type(row['ts']) is not int or ('holiday' in row and type(row['holiday']) is not bool):
                        raise ValueError
                    number(row['kwh'])
                    datetime.fromtimestamp(row['ts'], ZoneInfo(pack['timezone']))
                if not math.isclose(sum(r['kwh'] for r in profile), expected, abs_tol=0.001):
                    raise ValueError
        if marginal_hourly is not None and hourly is None:
            raise ValueError
    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        raise BillingError('invalid usage profile') from None
    plan = selected_plan(pack, plan_id)
    supply = None
    if plan['kind'] != 'bundled':
        supply = next((p for p in pack['supply'] if p['id'] == supply_id), None)
        if supply_id is not None and supply is None:
            raise BillingError('unknown supply')
    tou_components = [c for group in (plan, supply) if group for c in group['components'] if c['type'] == 'tou']
    if tou_components and hourly is None:
        calendars = {json.dumps([{k: v for k, v in p.items() if k not in ('name', 'rate')} for p in c['periods']], sort_keys=True)
                     for c in tou_components}
        if len(calendars) > 1:
            raise BillingError('different time-of-use calendars need an hourly profile')
    t = _calculate(pack, plan, supply, kwh, hourly, on_peak_kwh, months, days)
    # Differences use unrounded estimates, avoiding cent quantization in marginal rates.
    tou = bool(tou_components)
    tz = ZoneInfo(pack['timezone'])
    def extra(on):
        next_profile = None
        if hourly is not None:
            if marginal_hourly is not None:
                next_profile = list(hourly) + list(marginal_hourly)
            elif kwh:
                next_profile = [dict(r, kwh=r['kwh'] * (kwh + 1) / kwh) for r in hourly]
            else:
                anchor = int(datetime.combine(date.fromisoformat(plan['effective']), datetime.min.time(), tz).timestamp())
                next_profile = [{'ts': anchor, 'kwh': 1.0}]
        return _calculate(pack, plan, supply, kwh + 1, next_profile, on, months, days)['total'] - t['total']
    out = {k: round(t[k], 2) for k in ('fixed', 'delivery', 'supply', 'tax', 'total')}
    peak = on_peak_kwh
    if tou and hourly is not None:
        peak = sum(r['kwh'] for r in hourly if any(period_matches(datetime.fromtimestamp(r['ts'], tz), p, r.get('holiday', False))
                                                   for p in tou_components[0]['periods']))
    out.update(plan=plan_id, plan_label=plan['label'], kwh=round(kwh, 2), on_peak_kwh=round(peak, 2) if tou else None,
               effective_cents_per_kwh=round(t['total'] / kwh * 100, 2) if kwh else None,
               lines=[dict(l, amount=round(l['amount'], 2)) for l in t['lines']])
    if tou and hourly is None:
        on, off = extra(on_peak_kwh + 1), extra(on_peak_kwh)
        share = on_peak_kwh / kwh if kwh else 0
        out.update(marginal_on_cents=round(on * 100, 2), marginal_off_cents=round(off * 100, 2),
                   marginal_cents_per_kwh=round((share * on + (1 - share) * off) * 100, 2))
    else:
        out['marginal_cents_per_kwh'] = round(extra(None) * 100, 2)
        if tou:
            # Extremes within the profile's local calendar week; keep legacy live-rate fields.
            anchor = hourly[0]['ts'] if hourly else int(datetime.combine(date.fromisoformat(plan['effective']), datetime.min.time(), tz).timestamp())
            candidates = range(anchor, anchor + 7 * 86400, 3600)
            def combined_rate(ts):
                return sum(tou_rate(c, datetime.fromtimestamp(ts, tz)) for c in tou_components)
            for key, ts in [('marginal_on_cents', max(candidates, key=combined_rate)),
                            ('marginal_off_cents', min(candidates, key=combined_rate))]:
                profile = list(hourly) + [{'ts': ts, 'kwh': 1.0}]
                amount = _calculate(pack, plan, supply, kwh + 1, profile, None, months, days)['total'] - t['total']
                out[key] = round(amount * 100, 2)
    return out
