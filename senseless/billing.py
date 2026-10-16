"""Bills the way the utility writes them: a fixed monthly charge, delivery per kWh (flat or time-of-use),
supply per kWh, and sales tax above an exempt monthly amount. Sense's own "cost" is one flat rate per kWh,
which in Maine misses the delivery half of the bill.

Presets carry the published tariffs (with their source and effective date) so a bill can be estimated
for any kWh, and the current billing cycle can be projected and compared across rate plans. Settings and
entered bills live in settings.db.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from .summary import local_tz

# Published tariffs. Delivery includes transmission and Efficiency Maine charges (CMP's sheets say so).
PLANS = {
    "cmp-a": {
        "label": "CMP Rate A (standard residential)",
        "fixed": 27.37, "fixed_kwh": 50, "delivery": 0.119590, "tou": None,
        "source": "CMP Electric Delivery Rate Schedule page 10.00, 106th revision", "effective": "2026-07-01",
    },
    "cmp-a-tou": {
        "label": "CMP Residential Time-of-Use",
        "fixed": 24.18, "fixed_kwh": 0, "delivery": None,
        "tou": {"on": 0.428836, "off": 0.061470, "start_hour": 17, "end_hour": 21, "weekdays_only": True},
        "source": "cmpco.com/time-of-use-delivery-rate (on-peak 5-9 pm weekdays; holidays not modelled)", "effective": "2026-07-01",
    },
    "cmp-et": {
        "label": "CMP Electric Technology rate",
        "fixed": 41.24, "fixed_kwh": 0, "delivery": 0.088638, "tou": None,
        "source": "cmpco.com Electric Technology Rate page (unverified against the tariff sheet; expires Jan 2027)", "effective": "2026-07-01",
    },
    "custom": {
        "label": "Custom", "fixed": 0.0, "fixed_kwh": 0, "delivery": 0.0, "tou": None, "source": "entered by you", "effective": None,
    },
}

SUPPLY = {"label": "Maine Standard Offer (CMP residential)", "per_kwh": 0.127210,
          "source": "Maine PUC docket 2025-00157", "effective": "2026-01-01", "until": "2026-12-31"}

DEFAULTS = {
    "plan": "cmp-a",
    "supply_per_kwh": SUPPLY["per_kwh"],
    "supplier": SUPPLY["label"],
    "billing_day": 1,            # day of month the cycle starts (your meter-read date)
    "tax_rate": 0.055,           # Maine sales tax
    "tax_exempt_kwh": 750,       # first 750 kWh/month of residential electricity is exempt
    "custom": {"fixed": 0.0, "fixed_kwh": 0, "delivery": 0.0},
    "calibrate": False,          # scale estimates by how far off they were on entered bills
}

NUM_LIMITS = {"supply_per_kwh": (0, 2), "tax_rate": (0, 0.5), "tax_exempt_kwh": (0, 100000)}


class BillingError(ValueError):
    pass


def plan_for(settings: dict, plan_id: str | None = None) -> dict:
    pid = plan_id or settings["plan"]
    if pid not in PLANS:
        raise BillingError(f"unknown plan {pid}")
    p = dict(PLANS[pid], id=pid)
    if pid == "custom":
        p.update(settings.get("custom") or {})
    return p


def _total(p: dict, settings: dict, kwh: float, on: float | None, months: float) -> dict:
    fixed = p["fixed"] * months
    if p["tou"]:
        on = min(max(on, 0.0), kwh)
        delivery = on * p["tou"]["on"] + (kwh - on) * p["tou"]["off"]
    else:
        delivery = max(0.0, kwh - p["fixed_kwh"] * months) * p["delivery"]
    supply = kwh * settings["supply_per_kwh"]
    before_tax = fixed + delivery + supply
    # the exemption covers the first N kWh of energy and delivery alike: tax the share above it
    exempt = settings["tax_exempt_kwh"] * months
    taxable_share = max(0.0, kwh - exempt) / kwh if kwh > 0 else 0.0
    tax = before_tax * taxable_share * settings["tax_rate"]
    return {"fixed": fixed, "delivery": delivery, "supply": supply, "tax": tax, "total": before_tax + tax}


def estimate(settings: dict, kwh: float, on_peak_kwh: float | None = None, plan_id: str | None = None,
             months: float = 1.0) -> dict:
    """One bill for `kwh` used over `months` (1 = a normal cycle; fixed charge, included kWh and the tax
    exemption scale). The marginal rate is what one more kWh would add to this same bill."""
    if kwh < 0:
        raise BillingError("kwh must not be negative")
    p = plan_for(settings, plan_id)
    if p["tou"] and on_peak_kwh is None:
        raise BillingError("time-of-use needs on_peak_kwh (or an on-peak share)")
    t = _total(p, settings, kwh, on_peak_kwh, months)
    r2 = lambda x: round(x, 2)
    out = {"plan": p["id"], "plan_label": p["label"], "kwh": round(kwh, 2), "on_peak_kwh": round(on_peak_kwh, 2) if p["tou"] else None,
           "fixed": r2(t["fixed"]), "delivery": r2(t["delivery"]), "supply": r2(t["supply"]), "tax": r2(t["tax"]), "total": r2(t["total"]),
           "effective_cents_per_kwh": round(t["total"] / kwh * 100, 2) if kwh else None}
    if p["tou"]:
        share = on_peak_kwh / kwh if kwh else 0.0
        on = _total(p, settings, kwh + 1, on_peak_kwh + 1, months)["total"] - t["total"]
        off = _total(p, settings, kwh + 1, on_peak_kwh, months)["total"] - t["total"]
        out.update(marginal_cents_per_kwh=round((share * on + (1 - share) * off) * 100, 2),
                   marginal_on_cents=round(on * 100, 2), marginal_off_cents=round(off * 100, 2))
    else:
        out["marginal_cents_per_kwh"] = round((_total(p, settings, kwh + 1, None, months)["total"] - t["total"]) * 100, 2)
    return out


def bill_months(start: date, end: date) -> float:
    """A normal monthly bill (26-35 days) is one month of fixed charge and allowances; others prorate."""
    days = (end - start).days
    return 1.0 if 26 <= days <= 35 else days / 30.44


def cycle_bounds(billing_day: int, today: date) -> tuple[date, date]:
    """[start, end) of the billing cycle containing today; the cycle starts on billing_day each month."""
    start = today.replace(day=billing_day) if today.day >= billing_day else \
        (today.replace(day=1) - timedelta(days=1)).replace(day=billing_day)
    end = (start.replace(day=1) + timedelta(days=32)).replace(day=billing_day)
    return start, end


def is_on_peak(dt: datetime, tou: dict) -> bool:
    if tou.get("weekdays_only") and dt.weekday() >= 5:
        return False
    return tou["start_hour"] <= dt.hour < tou["end_hour"]


class Billing:
    def __init__(self, settings_db: Path, history=None, clock=time.time):
        self.db_path = settings_db
        self.history = history
        self.clock = clock
        self._lock = threading.Lock()
        with self._conn() as db:
            db.execute("CREATE TABLE IF NOT EXISTS billing (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT, start TEXT NOT NULL, end TEXT NOT NULL,
                kwh REAL NOT NULL, amount REAL NOT NULL, created REAL NOT NULL)""")
            if "on_peak_kwh" not in {r[1] for r in db.execute("PRAGMA table_info(bills)")}:
                db.execute("ALTER TABLE bills ADD COLUMN on_peak_kwh REAL")

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
            raise BillingError("expected an object")
        unknown = set(patch) - set(DEFAULTS)
        if unknown:
            raise BillingError(f"unknown fields: {', '.join(sorted(unknown))}")
        with self._lock:
            s = self.settings()
            for k, v in patch.items():
                if k == "plan":
                    if v not in PLANS:
                        raise BillingError("plan must be one of " + ", ".join(PLANS))
                elif k == "supplier":
                    if not isinstance(v, str) or not v.strip() or len(v) > 60:
                        raise BillingError("supplier must be a short name")
                    v = v.strip()
                elif k == "billing_day":
                    if type(v) is not int or not 1 <= v <= 28:
                        raise BillingError("billing_day must be a whole number from 1 to 28")
                elif k == "calibrate":
                    if type(v) is not bool:
                        raise BillingError("calibrate must be true or false")
                elif k == "custom":
                    if not isinstance(v, dict) or set(v) - {"fixed", "fixed_kwh", "delivery"}:
                        raise BillingError("custom takes fixed, fixed_kwh and delivery")
                    for ck, cv in v.items():
                        if not _num(cv) or not 0 <= cv <= (10000 if ck == "fixed_kwh" else 1000 if ck == "fixed" else 2):
                            raise BillingError(f"custom.{ck} is out of range")
                    v = {**s["custom"], **v}
                else:
                    lo, hi = NUM_LIMITS[k]
                    if not _num(v) or not lo <= v <= hi:
                        raise BillingError(f"{k} must be a number from {lo} to {hi}")
                s[k] = v
            with self._conn() as db:
                db.execute("INSERT INTO billing (id, body) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (json.dumps(s),))
        return s

    # ── entered bills (to check the model against reality) ─────────────
    def bills(self) -> list[dict]:
        s = self.settings()
        with self._conn() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM bills ORDER BY start DESC")]
        tou = bool(plan_for(s)["tou"])
        for b in rows:
            months = bill_months(date.fromisoformat(b["start"]), date.fromisoformat(b["end"]))
            if tou and b.get("on_peak_kwh") is None:
                # can't price a time-of-use bill without its on-peak kWh: shown, but not compared or calibrated
                b["estimate"] = b["diff"] = b["ratio"] = None
                continue
            est = estimate(s, b["kwh"], on_peak_kwh=b.get("on_peak_kwh") if tou else None, months=months)
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
        with self._conn() as db:
            cur = db.execute("INSERT INTO bills (start, end, kwh, amount, created, on_peak_kwh) VALUES (?,?,?,?,?,?)",
                             (start.isoformat(), end.isoformat(), float(kwh), float(amount), time.time(),
                              float(on) if on is not None else None))
            bid = cur.lastrowid
        return next(b for b in self.bills() if b["id"] == bid)

    def remove_bill(self, bill_id: int) -> None:
        with self._conn() as db:
            if not db.execute("DELETE FROM bills WHERE id=?", (bill_id,)).rowcount:
                raise KeyError(bill_id)

    def calibration(self) -> float | None:
        ratios = [b["ratio"] for b in self.bills() if b["ratio"]]
        return round(sum(ratios) / len(ratios), 3) if ratios else None

    # ── the current cycle ───────────────────────────────────────────────
    def _daily_kwh(self, start: date, end: date, today: date) -> tuple[dict[date, float], float]:
        """kWh per day from Sense's cloud history (complete back to before Senseless existed), and what
        Sense's own flat-rate cost said for those days."""
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

    def _on_peak_share(self, tou: dict, today: date) -> tuple[float | None, int]:
        """Share of kWh in on-peak hours over the 7 days before today (one full week, so weekdays and the
        weekend are mixed as they will be; days before the cycle count too). Hourly from Sense's history."""
        on = total = 0.0
        n = 0
        for d in (today - timedelta(days=k) for k in range(1, 8)):
            body = self.history.get("day", d.isoformat())
            hours = body.get("kwh") or []
            if len(hours) < 23:
                continue
            for h, kwh in enumerate(hours):
                if kwh is None:
                    continue
                total += kwh
                if is_on_peak(datetime(d.year, d.month, d.day, min(h, 23)), tou):
                    on += kwh
            n += 1
        return (on / total if total else None), n

    def cycle(self) -> dict:
        if self.history is None:
            raise BillingError("history is not configured")
        s = self.settings()
        tz = local_tz()
        now = datetime.fromtimestamp(self.clock(), tz)
        today = now.date()
        start, end = cycle_bounds(s["billing_day"], today)
        daily, sense_cents = self._daily_kwh(start, end, today)
        so_far = sum(daily.values())
        days = (end - start).days
        # elapsed as a fraction of days, today counted by the hour
        elapsed = (today - start).days + (now.hour + now.minute / 60) / 24
        complete = [daily[d] for d in sorted(daily) if d < today]
        recent = complete[-7:]
        per_day = (sum(recent) / len(recent)) if recent else (so_far / elapsed if elapsed > 0 else 0.0)
        # past days Sense has no number for still used power: fill them at the recent rate
        missing = [start + timedelta(days=k) for k in range((today - start).days) if start + timedelta(days=k) not in daily]
        projected = so_far + per_day * len(missing) + per_day * max(0.0, days - elapsed)

        tou_plan = PLANS["cmp-a-tou"]["tou"]
        share, share_days = self._on_peak_share(tou_plan, today)
        factor = self.calibration() if s["calibrate"] else None

        def bill(kwh, plan_id=None):
            p = plan_for(s, plan_id)
            on = kwh * share if p["tou"] and share is not None else None
            if p["tou"] and on is None:
                return None
            b = estimate(s, kwh, on, plan_id)
            if factor:
                b["calibrated_total"] = round(b["total"] * factor, 2)
            return b

        current = bill(projected)
        self._last = {"kwh": projected, "share": share}      # rates() prices live usage at this bill's position
        compare = []
        for pid in ("cmp-a", "cmp-a-tou", "cmp-et"):
            b = bill(projected, pid)
            if b is not None:
                compare.append({"plan": pid, "label": PLANS[pid]["label"], "total": b["total"],
                                "delta": round(b["total"] - current["total"], 2) if current else None})
        return {
            "start": start.isoformat(), "end": end.isoformat(), "days": days, "elapsed_days": round(elapsed, 2),
            "daily": [{"date": d.isoformat(), "kwh": round(daily[d], 3)} for d in sorted(daily)],
            "kwh_so_far": round(so_far, 2), "kwh_per_day": round(per_day, 2), "kwh_projected": round(projected, 1),
            "missing_days": [d.isoformat() for d in missing],
            "so_far": bill(so_far) if so_far else None,
            "projected": current,
            "compare": compare,
            "on_peak_share": round(share, 3) if share is not None else None, "on_peak_days": share_days,
            "calibration": factor,
            "sense_cost_so_far": round(sense_cents / 100, 2) if sense_cents else None,
            "plan": plan_for(s), "supply": {**SUPPLY, "per_kwh": s["supply_per_kwh"], "label": s["supplier"]},
        }

    def rates(self) -> dict:
        """What one more kWh costs at this cycle's projected usage (from the last cycle() call; 1,000 kWh
        until there is one), and the fixed charge per day, for live cost readouts."""
        s = self.settings()
        p = plan_for(s)
        last = getattr(self, "_last", None) or {"kwh": 1000.0, "share": None}
        share = last["share"] if last["share"] is not None else 0.15
        e = estimate(s, last["kwh"], last["kwh"] * share if p["tou"] else None)
        out = {"marginal_cents_per_kwh": e["marginal_cents_per_kwh"], "fixed_per_day": round(p["fixed"] / 30.44, 3),
               "plan": p["id"], "plan_label": p["label"], "at_kwh": round(last["kwh"], 1)}
        if p["tou"]:
            out.update(marginal_on_cents=e["marginal_on_cents"], marginal_off_cents=e["marginal_off_cents"], tou=p["tou"])
        return out

    def price_today(self, kwh: float, hourly_w: list, day: date) -> tuple[float, dict]:
        """Energy cost of today's kWh: time-of-use splits it by hour (from the hourly averages), flat plans
        use the one marginal rate."""
        r = self.rates()
        if not r.get("tou"):
            return kwh * r["marginal_cents_per_kwh"] / 100, r
        weights = [(h, w) for h, w in enumerate(hourly_w or []) if w]
        total = sum(w for _, w in weights)
        on = sum(w for h, w in weights if is_on_peak(datetime(day.year, day.month, day.day, h), r["tou"])) / total if total else 0.0
        return kwh * (on * r["marginal_on_cents"] + (1 - on) * r["marginal_off_cents"]) / 100, r


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")
