"""Billing settings, cycles, bills and insights; energy modules own parsing/storage/pricing."""
import copy
import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from .summary import local_tz
from .energy import BillingError
from .energy import tariff
from .energy.usage import UsageStore
from .energy.importers import import_data as parse_import, preview as preview_import, check_bill as imported_bill_check
from .energy.importers.csv_generic import AVANGRID as METER_HEADER, parse as parse_csv, validate_profile
from .energy.importers.bill_cmp import parse_cmp_bill_text, parse_cmp_bill_pdf, check as _bill_check

MAX_IMPORT_BYTES = 20 * 1024 * 1024
BILL_COLUMNS = {"bill_date": "TEXT", "delivery_total": "REAL", "supply_total": "REAL",
                "delivery_tax": "REAL", "supply_tax": "REAL", "fixed_total": "REAL",
                "supply_rate": "REAL", "segments_json": "TEXT", "source": "TEXT DEFAULT 'manual'",
                "lines_json": "TEXT", "tariff_json": "TEXT"}
_CMP = tariff.builtin_packs()['us-me-cmp']


def _legacy_plan(plan, pid=None):
    components = plan['components']
    fixed = sum(c['amount'] * (30.44 if c['per'] == 'day' else 1) for c in components if c['type'] == 'fixed')
    allowance = sum(c.get('includes_kwh', 0) for c in components if c['type'] == 'fixed')
    energy = next((c['rate'] for c in components if c['type'] == 'energy'), 0.0)
    component = next((c for c in components if c['type'] == 'tou'), None)
    tou = None
    if component:
        first = component['periods'][0]
        tou = {'on': first['rate'], 'off': component['default_rate'], 'start_hour': first['hours'][0],
               'end_hour': first['hours'][1], 'weekdays_only': first.get('weekdays') == [0, 1, 2, 3, 4],
               'periods': component['periods']}
    return {'id': pid or plan['id'], 'label': plan['label'], 'fixed': fixed, 'fixed_kwh': allowance,
            'delivery': None if tou else energy, 'tou': tou, 'source': plan['source'],
            'effective': plan['effective'], 'components': components,
            **({'expires': plan['expires']} if 'expires' in plan else {})}


# Compatibility catalogs are derived from data; existing consumers can still use cmp-* ids.
PLANS = {old: _legacy_plan(tariff.selected_plan(_CMP, old), old) for old in tariff.ALIASES}
PLANS['custom'] = {'id': 'custom', 'label': 'Custom', 'fixed': 0.0, 'fixed_kwh': 0,
                   'delivery': 0.0, 'tou': None, 'source': 'entered by you', 'effective': None}
_supply = _CMP['supply'][0]
SUPPLY = {'label': _supply['label'], 'per_kwh': _supply['components'][0]['rate'],
          'source': _supply['source'], 'effective': _supply['effective'], 'until': _supply['expires']}
DEFAULTS = {'pack': 'us-me-cmp', 'plan': 'rate-a', 'supply': 'standard-offer-2026',
            'supply_per_kwh': SUPPLY['per_kwh'], 'supplier': SUPPLY['label'], 'billing_day': 1,
            'tax_rate': _CMP['taxes'][0]['rate'], 'tax_exempt_kwh': _CMP['taxes'][0]['exempt_kwh'],
            'custom': {'fixed': 0.0, 'fixed_kwh': 0, 'delivery': 0.0}, 'calibrate': False}
NUM_LIMITS = {'supply_per_kwh': (0, 2), 'tax_rate': (0, 0.5), 'tax_exempt_kwh': (0, 100000)}


def parse_meter_csv(text):
    """Compatibility wrapper; new imports retain intervals through the registry."""
    rows = parse_csv(text.encode())['rows']
    return [(ts, kwh, temp, kwh + (received or 0), received) for ts, seconds, kwh, temp, received in rows]


def _custom_plan(settings):
    custom = {**DEFAULTS['custom'], **settings.get('custom', {})}
    return {'id': 'custom', 'label': 'Custom', 'kind': 'delivery', 'effective': '1970-01-01',
            'source': 'entered by you', 'components': [
                {'type': 'fixed', 'per': 'month', 'amount': custom['fixed'], 'includes_kwh': custom['fixed_kwh']},
                {'type': 'energy', 'rate': custom['delivery']}]}


def pricing_pack(settings, pack=None):
    result = copy.deepcopy(pack or _CMP)
    if result['id'] == 'us-me-cmp':
        result['plans'].append(_custom_plan(settings))
        # Preserve existing numeric overrides for CMP/custom. Other packs use their data unchanged.
        for supply in result['supply']:
            if supply['id'] == settings.get('supply', DEFAULTS['supply']):
                supply['components'] = [{'type': 'energy', 'rate': settings.get('supply_per_kwh', SUPPLY['per_kwh'])}]
        result['taxes'][0].update(rate=settings.get('tax_rate', DEFAULTS['tax_rate']),
                                  exempt_kwh=settings.get('tax_exempt_kwh', DEFAULTS['tax_exempt_kwh']))
    return result


def plan_for(settings, plan_id=None, pack=None):
    pid = plan_id or settings['plan']
    return _legacy_plan(tariff.selected_plan(pricing_pack(settings, pack), pid), pid)


def estimate(settings, kwh, on_peak_kwh=None, plan_id=None, months=1.0, *, pack=None, hourly=None):
    return tariff.estimate(pricing_pack(settings, pack), plan_id or settings['plan'],
                           settings.get('supply', DEFAULTS['supply']), kwh,
                           on_peak_kwh=on_peak_kwh, months=months, hourly=hourly)


def bill_months(start: date, end: date) -> float:
    """A normal monthly bill (26-35 days) is one month of fixed charge and allowances; others prorate."""
    days = (end - start).days
    return 1.0 if 26 <= days <= 35 else days / 30.44


def _on_day(month_start: date, billing_day: int) -> date:
    """billing_day of that month, clamped to its length (the 31st in February is the 28th or 29th)."""
    nxt = (month_start + timedelta(days=32)).replace(day=1)
    return month_start.replace(day=min(billing_day, (nxt - month_start).days))


def cycle_bounds(billing_day: int, today: date) -> tuple[date, date]:
    """[start, end) of the billing cycle containing today; the cycle starts on billing_day each month."""
    this = _on_day(today.replace(day=1), billing_day)
    start = this if today >= this else _on_day((today.replace(day=1) - timedelta(days=1)).replace(day=1), billing_day)
    end = _on_day((start.replace(day=1) + timedelta(days=32)).replace(day=1), billing_day)
    return start, end


def is_on_peak(dt: datetime, tou: dict) -> bool:
    if tou.get('periods'):
        return any(tariff.period_matches(dt, p) for p in tou['periods'])
    if tou.get("weekdays_only") and dt.weekday() >= 5:
        return False
    return tou["start_hour"] <= dt.hour < tou["end_hour"]


class Billing:
    def __init__(self, settings_db: Path, history=None, clock=time.time, local=None):
        self.db_path = settings_db
        self.history = history
        # Senseless's own readings, preferred over Sense's cloud history: callable(start_ts, end_ts, now) ->
        # {date: {kwh, coverage, hourly}} (summary.local_days). Sense may shut its servers down; this keeps the
        # bill working on data that lives here.
        self.local = local
        self.clock = clock
        self._lock = threading.Lock()
        with self._conn() as db:
            db.execute("CREATE TABLE IF NOT EXISTS billing (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL, end TEXT NOT NULL,
                kwh REAL NOT NULL, amount REAL NOT NULL, created REAL NOT NULL)""")
            if "on_peak_kwh" not in {r[1] for r in db.execute("PRAGMA table_info(bills)")}:
                db.execute("ALTER TABLE bills ADD COLUMN on_peak_kwh REAL")
            columns = {r[1] for r in db.execute("PRAGMA table_info(bills)")}
            for name, kind in BILL_COLUMNS.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE bills ADD COLUMN {name} {kind}")
            # Backfill normalized lines for the already-shipped CMP import schema.
            for row in db.execute("SELECT * FROM bills WHERE source='bill-pdf'").fetchall():
                bill = dict(row)
                lines = []
                for segment in json.loads(bill.get('segments_json') or '[]'):
                    lines.extend([
                        {'label': 'Fixed delivery', 'kind': 'fixed', 'amount': segment['fixed']},
                        {'label': 'Delivery energy', 'kind': 'delivery',
                         'amount': round((segment['kwh'] - segment['fixed_kwh']) * segment['delivery_rate'], 2),
                         'rate': segment['delivery_rate'], 'start': segment['start'], 'end': segment['end']}])
                delivery_lines = [line for line in lines if line['kind'] == 'delivery']
                if delivery_lines and bill.get('delivery_total') is not None:
                    # Retain the printed aggregate when rounded historical rates differ by a cent.
                    difference = bill['delivery_total'] - (bill.get('delivery_tax') or 0) - sum(line['amount'] for line in lines)
                    delivery_lines[-1]['amount'] = round(delivery_lines[-1]['amount'] + difference, 2)
                if bill.get('supply_total') is not None:
                    lines.append({'label': 'Supply energy', 'kind': 'supply',
                                  'amount': round(bill['supply_total'] - (bill.get('supply_tax') or 0), 2)})
                for field, label in [('delivery_tax', 'Delivery tax'), ('supply_tax', 'Supply tax')]:
                    if bill.get(field) is not None:
                        lines.append({'label': label, 'kind': 'tax', 'amount': bill[field]})
                db.execute("UPDATE bills SET lines_json=?, source='bill-cmp' WHERE id=?",
                           (json.dumps(lines), bill['id']))
            db.execute('CREATE TABLE IF NOT EXISTS csv_profiles (name TEXT PRIMARY KEY, body TEXT NOT NULL)')
            for row in db.execute('SELECT name, body FROM csv_profiles').fetchall():
                value = json.loads(row['body'])
                if 'match_headers' in value:
                    db.execute('UPDATE csv_profiles SET body=? WHERE name=?',
                               (json.dumps(validate_profile(value)), row['name']))
            db.execute('CREATE TABLE IF NOT EXISTS user_tariffs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.usage = UsageStore(self._conn)
        self.tariffs = tariff.Tariffs(self._conn)
        self._migrate_settings()

    def _migrate_settings(self):
        with self._conn() as db:
            row = db.execute('SELECT body FROM billing WHERE id=1').fetchone()
            if row:
                saved = json.loads(row['body'])
                saved.setdefault('pack', 'us-me-cmp')
                saved.setdefault('supply', 'standard-offer-2026')
                if saved['pack'] == 'us-me-cmp':
                    saved['plan'] = tariff.ALIASES.get(saved.get('plan'), saved.get('plan', 'rate-a'))
                db.execute('UPDATE billing SET body=? WHERE id=1', (json.dumps(saved),))
                if saved.get('custom'):
                    db.execute('INSERT OR IGNORE INTO user_tariffs VALUES (?,?)', ('custom', json.dumps(_custom_plan(saved))))

    def csv_profiles(self):
        with self._conn() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM csv_profiles ORDER BY name')]

    def save_csv_profile(self, value):
        profile = validate_profile(value)
        with self._lock, self._conn() as db:
            db.execute('INSERT INTO csv_profiles VALUES (?,?) ON CONFLICT(name) DO UPDATE SET body=excluded.body',
                       (profile['name'], json.dumps(profile)))
        return profile

    def remove_csv_profile(self, name):
        with self._conn() as db:
            if not db.execute('DELETE FROM csv_profiles WHERE name=?', (name,)).rowcount:
                raise KeyError(name)

    def preview_import(self, data):
        if len(data) > MAX_IMPORT_BYTES:
            raise BillingError('upload exceeds 20 MiB')
        return preview_import(data, self.csv_profiles())

    def import_data(self, data: bytes) -> dict:
        if len(data) > MAX_IMPORT_BYTES:
            raise BillingError('upload exceeds 20 MiB')
        result = parse_import(data, self.csv_profiles())
        metadata = {k: result[k] for k in ('importer', 'label')}
        if result['kind'] == 'bill':
            return {'kind': 'bill-pdf', **self._save_bill(result['bill']), **metadata}
        return {'kind': 'meter-csv', **self.usage.save(result['rows'], result['source']), **metadata}

    def _pricing_pack(self, settings):
        pack = pricing_pack(settings, self.tariffs.get(settings['pack']))
        if settings['pack'] == 'us-me-cmp':
            with self._conn() as db:
                row = db.execute("SELECT body FROM user_tariffs WHERE id='custom'").fetchone()
            if row:
                pack['plans'] = [p for p in pack['plans'] if p['id'] != 'custom'] + [json.loads(row['body'])]
        return pack

    def estimate(self, kwh, on_peak_kwh=None, plan_id=None, months=1.0, *, hourly=None, days=None):
        settings = self.settings()
        return tariff.estimate(self._pricing_pack(settings), plan_id or settings['plan'], settings['supply'], kwh,
                               on_peak_kwh=on_peak_kwh, months=months, hourly=hourly, days=days)

    def plan(self, settings=None, plan_id=None):
        settings = settings or self.settings()
        pack = self._pricing_pack(settings)
        selected = tariff.selected_plan(pack, plan_id or settings['plan'])
        view = _legacy_plan(selected, plan_id or settings['plan'])
        if selected['kind'] != 'bundled':
            supply = next((item for item in pack['supply'] if item['id'] == settings['supply']), None)
            if supply:
                view['fixed'] += sum(c['amount'] * (30.44 if c['per'] == 'day' else 1) for c in supply['components'] if c['type'] == 'fixed')
                if not view['tou']:
                    view['tou'] = _legacy_plan(supply)['tou']
        return view

    def plans(self):
        current = {pid: self.plan(plan_id=pid) for pid in self.plan_ids()}
        return {**PLANS, **current} if self.settings()['pack'] == 'us-me-cmp' else current

    def supply_info(self):
        settings = self.settings()
        pack = self.tariffs.get(settings['pack'])
        plan = tariff.selected_plan(pricing_pack(settings, pack), settings['plan'])
        if plan['kind'] == 'bundled' or settings['supply'] is None:
            return None
        item = next(p for p in pack['supply'] if p['id'] == settings['supply'])
        if settings['pack'] == 'us-me-cmp':
            return {**SUPPLY, 'per_kwh': settings['supply_per_kwh'], 'label': settings['supplier']}
        energy = [c['rate'] for c in item['components'] if c['type'] in ('energy', 'rider')]
        return {'id': item['id'], 'label': item['label'], 'source': item['source'], 'effective': item['effective'],
                'until': item.get('expires'), 'per_kwh': sum(energy) if energy else None,
                'components': item['components']}

    def plan_ids(self, settings=None):
        settings = settings or self.settings()
        ids = [p['id'] for p in self.tariffs.get(settings['pack'])['plans']]
        if settings['pack'] == 'us-me-cmp' and (settings['plan'] == 'custom' or any(settings['custom'].values())):
            ids.append('custom')
        return ids

    def save_tariff(self, value):
        pack = tariff.validate_pack(value)
        with self._lock:
            settings = self.settings()
            if settings['pack'] == pack['id']:
                plan = tariff.selected_plan(pack, settings['plan'])
                if plan['kind'] != 'bundled' and settings['supply'] not in {p['id'] for p in pack['supply']}:
                    if settings['supply'] is not None or pack['supply']:
                        raise BillingError('replacement removes the selected supply')
                if plan['kind'] == 'bundled' and settings['supply'] is not None:
                    raise BillingError('replacement changes the selected plan kind')
            return self.tariffs.save(pack)

    def remove_tariff(self, pack_id):
        with self._lock:
            if self.settings()['pack'] == pack_id and pack_id not in self.tariffs.builtins:
                raise BillingError('cannot delete the selected tariff pack')
            self.tariffs.remove(pack_id)

    def _save_bill(self, values: dict) -> dict:
        values = dict(values)
        if 'lines' in values:
            values['lines_json'] = json.dumps(values.pop('lines'))
        # All callers pass only normalized, explicitly selected fields.
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id FROM bills WHERE start=? AND end=? ORDER BY id DESC",
                                  (values["start"], values["end"])).fetchone()
            values = {"on_peak_kwh": None, **{name: None for name in BILL_COLUMNS}, **values, "created": self.clock()}
            fields = list(values)
            if previous:
                bid = previous["id"]
                db.execute(f"UPDATE bills SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*values.values(), bid))
                # Collapse legacy duplicates only for the period being explicitly replaced.
                db.execute("DELETE FROM bills WHERE start=? AND end=? AND id!=?", (values["start"], values["end"], bid))
            else:
                cur = db.execute(f"INSERT INTO bills ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", tuple(values.values()))
                bid = cur.lastrowid
        return next(b for b in self.bills() if b["id"] == bid)

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # ── settings ────────────────────────────────────────────────────────
    def settings(self) -> dict:
        with self._conn() as db:
            row = db.execute("SELECT body FROM billing WHERE id=1").fetchone()
        saved = json.loads(row["body"]) if row else {}
        out = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
        out["custom"] = {**DEFAULTS["custom"], **(saved.get("custom") or {})}
        return out

    def update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise BillingError('expected an object')
        if set(patch) - set(DEFAULTS):
            raise BillingError('unknown billing settings fields')
        with self._lock:
            s = self.settings()
            for k, v in patch.items():
                if k in ('pack', 'plan', 'supply'):
                    if not isinstance(v, str) and not (k == 'supply' and v is None):
                        raise BillingError('invalid tariff selection')
                elif k == 'supplier':
                    if not isinstance(v, str) or not v.strip() or len(v) > 60:
                        raise BillingError('supplier must be a short name')
                    v = v.strip()
                elif k == 'billing_day':
                    if type(v) is not int or not 1 <= v <= 31:
                        raise BillingError('billing_day must be a whole number from 1 to 31')
                elif k == 'calibrate':
                    if type(v) is not bool:
                        raise BillingError('calibrate must be true or false')
                elif k == 'custom':
                    if not isinstance(v, dict) or set(v) - {'fixed', 'fixed_kwh', 'delivery'}:
                        raise BillingError('custom takes fixed, fixed_kwh and delivery')
                    for ck, cv in v.items():
                        if not _num(cv) or not 0 <= cv <= (10000 if ck == 'fixed_kwh' else 1000 if ck == 'fixed' else 2):
                            raise BillingError('custom rate is out of range')
                    v = {**s['custom'], **v}
                else:
                    lo, hi = NUM_LIMITS[k]
                    if not _num(v) or not lo <= v <= hi:
                        raise BillingError('billing number is out of range')
                s[k] = v
            pack = self.tariffs.get(s['pack'])
            if 'pack' in patch and 'plan' not in patch:
                s['plan'] = pack['plans'][0]['id']
            if 'pack' in patch and 'supply' not in patch:
                s['supply'] = pack['supply'][0]['id'] if pack['supply'] else None
            if s['pack'] == 'us-me-cmp':
                s['plan'] = tariff.ALIASES.get(s['plan'], s['plan'])
            plan = tariff.selected_plan(pricing_pack(s, pack), s['plan'])
            if plan['kind'] == 'bundled':
                s['supply'] = None
            elif s['supply'] not in {item['id'] for item in pack['supply']}:
                if s['supply'] is not None or pack['supply']:
                    raise BillingError('unknown supply')
            if s['pack'] != 'us-me-cmp' and set(patch) & {'supply_per_kwh', 'tax_rate', 'tax_exempt_kwh', 'custom'}:
                raise BillingError('edit the user tariff pack to change its components')
            with self._conn() as db:
                db.execute('INSERT INTO billing (id, body) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (json.dumps(s),))
                if s['pack'] == 'us-me-cmp' and ('custom' in patch or s['plan'] == 'custom'):
                    db.execute('INSERT INTO user_tariffs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                               ('custom', json.dumps(_custom_plan(s))))
        return s

    # ── entered bills (to check the model against reality) ─────────────
    def bills(self) -> list[dict]:
        s = self.settings()
        with self._conn() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM bills ORDER BY start DESC")]
        tou = bool(self.plan(s)["tou"])
        for b in rows:
            b["lines"] = json.loads(b.get("lines_json") or "[]")
            months = bill_months(date.fromisoformat(b["start"]), date.fromisoformat(b["end"]))
            selected = self.plan(s)
            if tou and (b.get('on_peak_kwh') is None or len(selected['tou'].get('periods', [])) > 1):
                # can't price a time-of-use bill without its on-peak kWh: shown, but not compared or calibrated
                b["estimate"] = b["diff"] = b["ratio"] = None
                continue
            try:
                est = self.estimate(b['kwh'], on_peak_kwh=b.get('on_peak_kwh') if tou else None, months=months,
                                    days=(date.fromisoformat(b['end']) - date.fromisoformat(b['start'])).days)
            except BillingError:
                if not tou:
                    raise
                b['estimate'] = b['diff'] = b['ratio'] = None
                continue
            b["estimate"] = est["total"]
            b["diff"] = round(b["amount"] - est["total"], 2)
            b["ratio"] = round(b["amount"] / est["total"], 3) if est["total"] else None
        return rows

    def add_bill(self, body: dict) -> dict:
        try:
            start, end = date.fromisoformat(body.get("start", "")), date.fromisoformat(body.get("end", ""))
        except (TypeError, ValueError):
            raise BillingError("start and end must be YYYY-MM-DD") from None
        if not 7 <= (end - start).days <= 62:
            raise BillingError("a bill covers 7 to 62 days")
        kwh, amount, on = body.get("kwh"), body.get("amount"), body.get("on_peak_kwh")
        if not _num(kwh) or not 0 < kwh < 100000 or not _num(amount) or not 0 < amount < 100000:
            raise BillingError("kwh and amount must be positive numbers")
        if on is not None and (not _num(on) or not 0 <= on <= kwh):
            raise BillingError("on_peak_kwh must be a number from 0 to kwh")
        s = self.settings()
        p = self.plan(s)
        values = {"start": start.isoformat(), "end": end.isoformat(), "kwh": float(kwh), "amount": float(amount),
                  "on_peak_kwh": float(on) if on is not None else None, "source": "manual"}
        if s["pack"] == "us-me-cmp" and (not p["tou"] or on is not None):
            months = bill_months(start, end)
            segment = {"start": start.isoformat(), "end": end.isoformat(), "kwh": kwh,
                       "fixed": p["fixed"] * months, "fixed_kwh": p["fixed_kwh"] * months,
                       "delivery_rate": p["delivery"]}
            if p["tou"]:
                segment.update(tou=p["tou"], delivery_rate=(on * p["tou"]["on"] + (kwh - on) * p["tou"]["off"]) / kwh)
            values.update(fixed_total=p["fixed"] * months, supply_rate=s["supply_per_kwh"], segments_json=json.dumps([
                segment]))
        if s['pack'] != 'us-me-cmp':
            values['tariff_json'] = self._tariff_snapshot(s)
        return self._save_bill(values)

    def remove_bill(self, bill_id: int) -> None:
        with self._conn() as db:
            if not db.execute("DELETE FROM bills WHERE id=?", (bill_id,)).rowcount:
                raise KeyError(bill_id)

    def _tariff_snapshot(self, settings):
        pack = self._pricing_pack(settings)
        plan = tariff.selected_plan(pack, settings['plan'])
        supply = next((p for p in pack['supply'] if p['id'] == settings['supply'] and plan['kind'] != 'bundled'), None)
        fields = ('id', 'kind', 'effective', 'expires', 'components')
        return json.dumps({'pack': pack['id'], 'timezone': pack['timezone'],
                           'plan': {k: plan[k] for k in fields if k in plan},
                           'supply': {k: supply[k] for k in fields if k in supply} if supply else None,
                           'taxes': [{k: v for k, v in tax.items() if k != 'label'} for tax in pack['taxes']]}, sort_keys=True)

    def calibration(self) -> float | None:
        settings = self.settings()
        if settings['pack'] != 'us-me-cmp':
            snapshot = self._tariff_snapshot(settings)
            ratios = [b['ratio'] for b in self.bills() if b.get('tariff_json') == snapshot
                      and b['ratio'] is not None and self.check_bill(b) is not None]
            return round(sum(ratios) / len(ratios), 3) if ratios else None
        p = self.plan()
        ratios = []
        for b in self.bills():
            if not b["ratio"] or not b.get("segments_json"):
                continue
            if b["source"] == "manual" and p["effective"] and b["start"] < p["effective"]:
                # Manual entries only capture the selected tariff, not proof of an older one.
                continue
            segments = json.loads(b["segments_json"])
            months = bill_months(date.fromisoformat(b["start"]), date.fromisoformat(b["end"]))
            matching_rates = (all(seg.get("tou") == p["tou"] for seg in segments) if p["tou"] else
                              all(not seg.get("tou") and math.isclose(seg["delivery_rate"], p["delivery"], abs_tol=1e-8)
                                  for seg in segments))
            if math.isclose(b["fixed_total"], p["fixed"] * months, abs_tol=0.01) and matching_rates:
                ratios.append(b["ratio"])
        return round(sum(ratios) / len(ratios), 3) if ratios else None

    # ── meter-backed analysis ───────────────────────────────────────────
    def _meter_rows(self) -> list[dict]:
        # one page load asks for these several times (cycle, on-peak share, insights): rebuild only when the
        # stored intervals (or the tariff's time zone) changed, judged by a cheap fingerprint of the table
        settings = self.settings()
        tzname = self.tariffs.get(settings['pack'])['timezone']
        with self._conn() as db:
            key = (tzname, *db.execute('SELECT count(*), max(ts), total(kwh), total(seconds) FROM usage_interval').fetchone())
        cached = getattr(self, "_meter_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        rows = self.usage.hourly(ZoneInfo(tzname))
        self._meter_cache = (key, rows)
        return rows

    @staticmethod
    def _meter_days(rows: list[dict]) -> dict:
        days = {}
        tz = local_tz()
        for row in rows:
            day = datetime.fromtimestamp(row["ts"], tz).date()
            item = days.setdefault(day, {"kwh": 0.0, "temps": [], "hours": 0})
            item["kwh"] += row["kwh"]
            item["hours"] += row.get("coverage_seconds", 3600) / 3600
            if row["temp_f"] is not None:
                item["temps"].append(row["temp_f"])
        return days

    @staticmethod
    def _complete_day(day: date, item: dict) -> bool:
        tz = local_tz()
        begin = datetime.combine(day, datetime.min.time(), tz)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz)
        return item["hours"] == round((end.timestamp() - begin.timestamp()) / 3600)

    def _plans_12mo(self, rows: list[dict], today: date) -> dict | None:
        end = today.replace(day=1)
        start = end.replace(year=end.year - 1)
        tz = local_tz()
        window = [r for r in rows if start <= datetime.fromtimestamp(r["ts"], tz).date() < end]
        daily = self._meter_days(window)
        if sum(self._complete_day(d, item) for d, item in daily.items()) < 300:
            return None
        s = self.settings()
        plan_ids = self.plan_ids(s)
        tou_plan = next((self.plan(s, pid)['tou'] for pid in plan_ids if self.plan(s, pid)['tou']), None)
        monthly = {}
        for r in window:
            dt = datetime.fromtimestamp(r["ts"], tz)
            item = monthly.setdefault(dt.strftime("%Y-%m"), {"kwh": 0.0, "rows": []})
            item["kwh"] += r["kwh"]
            item['rows'].append({'ts': r['ts'], 'kwh': max(0, r['kwh'])})
        months = []
        d = start
        while d < end:
            key = d.strftime("%Y-%m")
            item = monthly.get(key, {"kwh": 0.0, "rows": []})
            kwh = max(0.0, item["kwh"])
            # Negative net-export readings are not consumption; preserve aggregate net kWh,
            # scaling positive hours to it so the engine gets a consistent profile.
            profile = self._scaled_profile(item['rows'], kwh)
            month_days = ((d + timedelta(days=32)).replace(day=1) - d).days
            on = sum(r['kwh'] for r in profile if tou_plan and is_on_peak(datetime.fromtimestamp(r['ts'], tz), tou_plan))
            totals = {pid: self.estimate(kwh, plan_id=pid, hourly=profile, days=month_days)['total'] for pid in plan_ids}
            months.append({"month": key, "kwh": round(kwh, 3), "on_peak_share": round(on / kwh, 5) if kwh else 0.0,
                           "totals": totals})
            d = (d + timedelta(days=32)).replace(day=1)
        return {"from": start.isoformat(), "to": end.isoformat(), "months": months,
                "totals": {pid: round(sum(m["totals"][pid] for m in months), 2) for pid in plan_ids}}

    def insights(self) -> dict:
        rows = self._meter_rows()
        today = datetime.fromtimestamp(self.clock(), local_tz()).date()
        daily = {d: item for d, item in self._meter_days(rows).items() if d < today and self._complete_day(d, item)}
        comparison = None
        if daily and self.history is not None:
            try:
                sense, _ = self._sense_daily(min(daily), max(daily) + timedelta(days=1), today - timedelta(days=1))
            except Exception:  # noqa: BLE001 - Sense unreachable: no comparison
                sense = {}
            both = sorted(d for d in daily if d in sense)
            if both:
                monthly = {}
                for d in both:
                    item = monthly.setdefault(d.strftime("%Y-%m"), {"meter_kwh": 0.0, "sense_kwh": 0.0})
                    item["meter_kwh"] += daily[d]["kwh"]
                    item["sense_kwh"] += sense[d]
                meter_kwh, sense_kwh = sum(daily[d]["kwh"] for d in both), sum(sense[d] for d in both)
                comparison = {"days": len(both), "meter_kwh": round(meter_kwh, 3), "sense_kwh": round(sense_kwh, 3),
                              "ratio": round(sense_kwh / meter_kwh, 6) if meter_kwh else None,
                              "months": [{"month": m, **{k: round(v, 3) for k, v in item.items()}} for m, item in sorted(monthly.items())]}
        bins = {}
        for item in daily.values():
            if item["temps"]:
                lower = math.floor(sum(item["temps"]) / len(item["temps"]) / 10) * 10
                bins.setdefault(lower, []).append(item["kwh"])
        bills = []
        for b in self.bills():
            bills.append({k: b[k] for k in ("start", "end", "kwh", "amount")})
            bills[-1].update(effective_cents_per_kwh=round(b["amount"] / b["kwh"] * 100, 2), check=self.check_bill(b))
        return {"meter": {"hours": len(rows), "from": rows[0]["ts"], "to": rows[-1]["ts"]} if rows else None,
                "sense_vs_meter": comparison, "plans_12mo": self._plans_12mo(rows, today),
                "weather": {"bins": [{"from_f": lo, "to_f": lo + 10, "days": len(values),
                                      "kwh_per_day": round(sum(values) / len(values), 3)} for lo, values in sorted(bins.items())]} if bins else None,
                "bills": bills}

    def check_bill(self, bill):
        own_check = imported_bill_check(bill)
        if own_check is not None:
            return own_check
        if bill.get('source') == 'bill-pdf':
            return _bill_check(bill)
        s = self.settings()
        if s['pack'] == 'us-me-cmp' and not bill.get('segments_json'):
            return None
        pack = self.tariffs.get(s['pack'])
        plan = tariff.selected_plan(pricing_pack(s, pack), s['plan'])
        choices = [plan] + [item for item in pack['supply'] if item['id'] == s['supply'] and plan['kind'] != 'bundled']
        if any(bill['start'] < item['effective'] or (item.get('expires') and bill['end'] > item['expires']) for item in choices):
            return None
        tou = self.plan(s)['tou']
        if tou and (bill.get('on_peak_kwh') is None or len(tou.get('periods', [])) > 1):
            return None
        months = bill_months(date.fromisoformat(bill['start']), date.fromisoformat(bill['end']))
        try:
            total = self.estimate(bill['kwh'], bill.get('on_peak_kwh'), months=months,
                                  days=(date.fromisoformat(bill['end']) - date.fromisoformat(bill['start'])).days)['total']
        except BillingError:
            if tou:
                return None
            raise
        return {'model_total': total, 'diff': round(bill['amount'] - total, 2)}

    def _seasonal_factor(self, rows: list[dict], today: date, end: date) -> float | None:
        daily = self._meter_days(rows)

        def last_year(d):
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # Feb 29 maps to Feb 28
                return d.replace(year=d.year - 1, day=28)

        def average(start, stop):
            days = [last_year(start + timedelta(days=i)) for i in range((stop - start).days)]
            if not days or any(d not in daily or not self._complete_day(d, daily[d]) for d in days):
                return None
            return sum(daily[d]["kwh"] for d in days) / len(days)

        recent, remaining = average(today - timedelta(days=7), today), average(today, end)
        if recent is None or remaining is None or recent <= 0:
            return None
        return min(1.6, max(0.6, remaining / recent))

    # ── the current cycle ───────────────────────────────────────────────
    LOCAL_COMPLETE = 0.97   # share of a day Senseless must have recorded to stand on its own

    def _local_days(self, start: date, end: date) -> dict:
        if self.local is None:
            return {}
        tz = local_tz()
        try:
            return self.local(datetime.combine(start, datetime.min.time(), tz).timestamp(),
                              datetime.combine(end, datetime.min.time(), tz).timestamp(), self.clock())
        except Exception:      # noqa: BLE001 - a local read problem falls back to the other sources
            return {}

    def _daily_kwh(self, start: date, end: date, today: date) -> tuple[dict[date, float], float, dict]:
        """kWh per day for [start, end), from the best source that has the whole day: Senseless's own readings,
        then imported utility-meter data, then Sense's cloud history (only for days before Senseless recorded,
        or gaps); a partly recorded day with no other source is scaled up from what was recorded. Also what Sense's
        flat-rate cost said (for comparison) and each day's source."""
        last = min(today, end - timedelta(days=1))
        local = self._local_days(start, last + timedelta(days=1))
        meter = {}
        if self.usage is not None:
            meter = {d: item["kwh"] for d, item in self._meter_days(self._meter_rows()).items()
                     if start <= d <= last and self._complete_day(d, item)}
        try:
            sense, sense_cents = self._sense_daily(start, end, today)
        except Exception:      # noqa: BLE001 - Sense's cloud unreachable: carry on with local data
            sense, sense_cents = {}, 0.0
        out, source = {}, {}
        d = start
        while d <= last:
            item = local.get(d)
            if item and item["coverage"] >= self.LOCAL_COMPLETE:
                out[d], source[d] = item["kwh"], "senseless"
            elif d in meter:
                out[d], source[d] = meter[d], "meter"
            elif d in sense:
                out[d], source[d] = sense[d], "sense"
            elif item and item["coverage"] >= 0.5:
                out[d], source[d] = item["kwh"] / item["coverage"], "senseless-estimated"
            d += timedelta(days=1)
        return out, sense_cents, source

    def _sense_daily(self, start: date, end: date, today: date) -> tuple[dict[date, float], float]:
        """kWh per day from Sense's cloud history (complete back to before Senseless existed), and what
        Sense's own flat-rate cost said for those days."""
        if self.history is None:
            return {}, 0.0
        out: dict[date, float] = {}
        sense_cents = 0.0
        months, d = [], start.replace(day=1)
        while d < end and d <= today:      # History refuses future periods
            months.append(d)
            d = (d + timedelta(days=32)).replace(day=1)
        for m in months:
            body = self.history.get("month", m.isoformat())
            cents = body.get("cost_cents") or []
            for i, kwh in enumerate(body.get("kwh") or []):
                day = m + timedelta(days=i)
                if start <= day <= today and day < end and kwh is not None:
                    out[day] = kwh
                    sense_cents += cents[i] if i < len(cents) and cents[i] is not None else 0
        return out, sense_cents

    def _day_hours(self, day: date, local: dict | None = None) -> list | None:
        """24 hourly kWh for a complete past day: Senseless's readings, else the utility meter, else Sense."""
        local = local if local is not None else self._local_days(day, day + timedelta(days=1))
        item = local.get(day)
        if item and item["coverage"] >= self.LOCAL_COMPLETE:
            return item["hourly"]
        if self.usage is not None:
            tz = local_tz()
            hours = [0.0] * 24
            seen = 0
            for row in self._meter_rows():
                dt = datetime.fromtimestamp(row["ts"], tz)
                if dt.date() == day:
                    hours[dt.hour] += row["kwh"]
                    seen += 1
            if seen >= 23:
                return hours
        if self.history is None:
            return None
        try:
            hours = self.history.get("day", day.isoformat()).get("kwh") or []
        except Exception:      # noqa: BLE001
            return None
        return hours if len(hours) >= 23 else None

    def _on_peak_share(self, tou: dict, today: date) -> tuple[float | None, int]:
        """Share of kWh in on-peak hours over the 7 days before today (one full week, so weekdays and the
        weekend are mixed as they will be; days before the cycle count too). Hourly from Sense's history."""
        on = total = 0.0
        n = 0
        local = self._local_days(today - timedelta(days=7), today)
        for d in (today - timedelta(days=k) for k in range(1, 8)):
            hours = self._day_hours(d, local)
            if hours is None:
                continue
            for h, kwh in enumerate(hours):
                if kwh is None:
                    continue
                total += kwh
                if is_on_peak(datetime(d.year, d.month, d.day, min(h, 23)), tou):
                    on += kwh
            n += 1
        return (on / total if total else None), n

    @staticmethod
    def _scaled_profile(rows, kwh):
        total = sum(row['kwh'] for row in rows)
        if total <= 0:
            return [] if kwh == 0 else None
        return [{'ts': row['ts'], 'kwh': row['kwh'] * kwh / total} for row in rows]

    def _recent_hourly(self, today):
        rows = []
        tz = local_tz()
        local = self._local_days(today - timedelta(days=7), today)
        for offset in range(1, 8):
            day = today - timedelta(days=offset)
            for hour, kwh in enumerate(self._day_hours(day, local) or []):
                if kwh is not None:
                    rows.append({'ts': int(datetime.combine(day, datetime.min.time(), tz).replace(hour=min(hour, 23)).timestamp()),
                                 'kwh': max(0, kwh)})
        return rows

    def cycle(self) -> dict:
        if self.history is None and self.local is None:
            raise BillingError("no usage source: neither Senseless readings nor Sense history is configured")
        s = self.settings()
        tz = local_tz()
        now = datetime.fromtimestamp(self.clock(), tz)
        today = now.date()
        start, end = cycle_bounds(s["billing_day"], today)
        daily, sense_cents, sources = self._daily_kwh(start, end, today)
        so_far = sum(daily.values())
        days = (end - start).days
        # elapsed as a fraction of days, today counted by the hour
        elapsed = (today - start).days + (now.hour + now.minute / 60) / 24
        recent = [daily[d] for d in sorted(daily) if today - timedelta(days=7) <= d < today]
        per_day = (sum(recent) / len(recent)) if recent else (so_far / elapsed if elapsed > 0 else 0.0)
        # past days Sense has no number for still used power: fill them at the recent rate
        missing = [start + timedelta(days=k) for k in range((today - start).days) if start + timedelta(days=k) not in daily]
        meter = self._meter_rows()
        seasonal = self._seasonal_factor(meter, today, end)
        projected = so_far + per_day * len(missing) + per_day * max(0.0, days - elapsed) * (seasonal if seasonal is not None else 1.0)
        projection_basis = "last year's meter data" if seasonal is not None else "last 7 days"

        tou_plan = next((self.plan(s, pid)['tou'] for pid in self.plan_ids(s) if self.plan(s, pid)['tou']), None)
        share, share_days = self._on_peak_share(tou_plan, today) if tou_plan else (None, 0)
        factor = self.calibration() if s["calibrate"] else None
        recent_profile = self._recent_hourly(today) if tou_plan else []

        def bill(kwh, plan_id=None):
            p = self.plan(s, plan_id)
            on = kwh * share if p["tou"] and share is not None else None
            if p["tou"] and on is None:
                return None
            hourly = self._scaled_profile(recent_profile, kwh) if p['tou'] else None
            if p['tou'] and hourly is None:
                return None
            b = self.estimate(kwh, on, plan_id, hourly=hourly, days=days)
            if factor:
                b["calibrated_total"] = round(b["total"] * factor, 2)
            return b

        current = bill(projected)
        self._last = {"kwh": projected, "share": share, "profile": recent_profile}      # rates() prices live usage at this bill's position
        compare = []
        annual = self._plans_12mo(meter, today)
        basis = "12 months of meter data" if annual else "last 7 days"
        for pid in self.plan_ids(s):
            b = {"total": annual["totals"][pid]} if annual else bill(projected, pid)
            reference = annual["totals"][s["plan"]] if annual else current["total"] if current else None
            if b is not None:
                compare.append({"plan": pid, "label": self.plan(s, pid)["label"], "total": b["total"],
                                "delta": round(b["total"] - reference, 2) if reference is not None else None, "basis": basis})
        return {
            "start": start.isoformat(), "end": end.isoformat(), "days": days, "elapsed_days": round(elapsed, 2),
            "daily": [{"date": d.isoformat(), "kwh": round(daily[d], 3), "source": sources.get(d)} for d in sorted(daily)],
            "kwh_so_far": round(so_far, 2), "kwh_per_day": round(per_day, 2), "kwh_projected": round(projected, 1),
            "projection_basis": projection_basis, "seasonal_factor": round(seasonal, 3) if seasonal is not None else None,
            "missing_days": [d.isoformat() for d in missing],
            "so_far": bill(so_far) if so_far else None,
            "projected": current,
            "compare": compare,
            "compare_basis": basis,
            "on_peak_share": round(share, 3) if share is not None else None, "on_peak_days": share_days,
            "calibration": factor,
            "sense_cost_so_far": round(sense_cents / 100, 2) if sense_cents else None,
            "day_sources": {k: sum(1 for v in sources.values() if v == k) for k in sorted(set(sources.values()))},
            "plan": self.plan(s), "supply": self.supply_info(),
        }

    def rates(self) -> dict:
        """What one more kWh costs at this cycle's projected usage (from the last cycle() call; 1,000 kWh
        until there is one), and the fixed charge per day, for live cost readouts."""
        s = self.settings()
        p = self.plan(s)
        last = getattr(self, "_last", None) or {"kwh": 1000.0, "share": None}
        share = last["share"] if last["share"] is not None else 0.15
        hourly = None
        if p['tou'] and s['pack'] != 'us-me-cmp':
            rows = last.get('profile')
            if not rows or sum(r['kwh'] for r in rows) <= 0:
                now = datetime.fromtimestamp(self.clock(), local_tz())
                start = datetime.combine(now.date() - timedelta(days=7), datetime.min.time(), local_tz()).timestamp()
                rows = [{'ts': int(start + h * 3600), 'kwh': 1.0} for h in range(7 * 24)]
            hourly = self._scaled_profile(rows, last['kwh'])
        e = self.estimate(last["kwh"], last["kwh"] * share if p["tou"] else None, hourly=hourly)
        out = {"marginal_cents_per_kwh": e["marginal_cents_per_kwh"], "fixed_per_day": round(p["fixed"] / 30.44, 3),
               "plan": p["id"], "plan_label": p["label"], "at_kwh": round(last["kwh"], 1)}
        if p["tou"]:
            out.update(marginal_on_cents=e["marginal_on_cents"], marginal_off_cents=e["marginal_off_cents"], tou=p["tou"])
        return out

    def price_today(self, kwh: float, hourly_w: list, day: date) -> tuple[float, dict]:
        """Energy cost of today's kWh: time-of-use splits it by hour (from the hourly averages), flat plans
        use the one marginal rate. Hourly average watts stand in for each hour's kWh, so the hour in progress
        weighs as a full one: close enough for a live readout; the cycle bill uses Sense's hourly kWh."""
        r = self.rates()
        if not r.get("tou"):
            return kwh * r["marginal_cents_per_kwh"] / 100, r
        weights = [(h, w) for h, w in enumerate(hourly_w or []) if w]
        total = sum(w for _, w in weights)
        settings = self.settings()
        if settings['pack'] != 'us-me-cmp':
            last = getattr(self, '_last', None) or {'kwh': 1000.0}
            rows = last.get('profile')
            tz = local_tz()
            start = int(datetime.combine(day, datetime.min.time(), tz).timestamp())
            if not rows or sum(r['kwh'] for r in rows) <= 0:
                rows = [{'ts': start - 7 * 86400 + h * 3600, 'kwh': 1.0} for h in range(168)]
            baseline = self._scaled_profile(rows, last['kwh'])
            increment = [{'ts': int(datetime.combine(day, datetime.min.time(), tz).replace(hour=h).timestamp()), 'kwh': w / total} for h, w in weights] if total else [{'ts': start, 'kwh': 1.0}]
            marginal = tariff.estimate(self._pricing_pack(settings), settings['plan'], settings['supply'], last['kwh'],
                                       hourly=baseline, marginal_hourly=increment)['marginal_cents_per_kwh']
            return kwh * marginal / 100, r
        on = sum(w for h, w in weights if is_on_peak(datetime(day.year, day.month, day.day, h), r["tou"])) / total if total else 0.0
        return kwh * (on * r["marginal_on_cents"] + (1 - on) * r["marginal_off_cents"]) / 100, r


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")
