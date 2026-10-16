"""Bills the way the utility writes them: a fixed monthly charge, delivery per kWh (flat or time-of-use),
supply per kWh, and sales tax above an exempt monthly amount. Sense's own "cost" is one flat rate per kWh,
which in Maine misses the delivery half of the bill.

Presets carry the published tariffs (with their source and effective date) so a bill can be estimated
for any kWh, and the current billing cycle can be projected and compared across rate plans. Settings and
entered bills live in settings.db.
"""

import csv
import io
import json
import logging
import math
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
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
MAX_IMPORT_BYTES = 20 * 1024 * 1024
METER_HEADER = "Name,Address,Account Number,Service,Type,Date,Start Time,End Time,Net,Units,Costs,Weather,Delivered,Received".split(",")
BILL_COLUMNS = {"bill_date": "TEXT", "delivery_total": "REAL", "supply_total": "REAL",
                "delivery_tax": "REAL", "supply_tax": "REAL", "fixed_total": "REAL",
                "supply_rate": "REAL", "segments_json": "TEXT", "source": "TEXT DEFAULT 'manual'"}


class BillingError(ValueError):
    pass


def _finite(text: str) -> float:
    value = float(text.replace(",", ""))
    if not math.isfinite(value):
        raise ValueError
    return value


def parse_meter_csv(text: str) -> list[tuple]:
    """Discard identity columns immediately; errors never include uploaded values."""
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
        if reader.fieldnames != METER_HEADER:
            raise ValueError
        rows = {}
        for row in reader:
            if None in row or any(v is None for v in row.values()) or row["Units"] != "kWh":
                raise ValueError
            start = datetime.fromisoformat(row["Start Time"])
            if start.utcoffset() is None or start.minute or start.second or start.microsecond:
                raise ValueError
            ts = int(start.timestamp())
            rows[ts] = (ts, _finite(row["Net"]), *(_finite(row[k]) if row[k].strip() else None
                                                  for k in ("Weather", "Delivered", "Received")))
        if not rows:
            raise ValueError
        return list(rows.values())
    except (ValueError, TypeError, OverflowError, csv.Error):
        raise BillingError("invalid hourly meter CSV (offset timestamps and kWh units required)") from None


def parse_cmp_bill_text(text: str) -> dict:
    """Pure CMP text parser. Only explicitly selected dates and numbers leave this function.

    Split bills sometimes omit printed rates: infer those from the segment's rounded charge/kWh.
    Never keep meter readings, identifiers, supplier/customer text, or the original document.
    """
    number = r"\d[\d,]*(?:\.\d+)?"
    day = r"\d{2}/\d{2}/\d{4}"
    text = " ".join(text.replace("–", "-").split())

    def match(pattern, source=text):
        found = re.search(pattern, source, re.I)
        if found is None:
            raise ValueError
        return found

    def iso(value):
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()

    try:
        bill_date = iso(match(rf"Bill Date\s+({day})")[1])
        meter = match(rf"\S+\s+({day})\s+{number}\s+({day})\s+{number}\s+(\d+)\s+({number})(?![\d,.])")
        end, start, days, kwh = iso(meter[1]), iso(meter[2]), int(meter[3]), _finite(meter[4])
        if not 7 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 62 or not 0 < kwh < 100000:
            raise ValueError
        if days != (date.fromisoformat(end) - date.fromisoformat(start)).days:
            raise ValueError
        delivery_end = match(rf"Total Current Delivery Charges\s+\+?\$\s*({number})")
        supply_end = match(rf"Total New Supplier Charges\s+\+?\$\s*({number})")
        headings = list(re.finditer(rf"Delivery Charges:\s*Residential\s*\(\s*({day})\s*-\s*({day})\s*\)", text, re.I))
        if not 1 <= len(headings) <= 2:
            raise ValueError
        segments = []
        for i, heading in enumerate(headings):
            body = text[heading.end():headings[i + 1].start() if i + 1 < len(headings) else delivery_end.start()]
            fixed = match(rf"Up to\s+({number})\s*KWH\s*(?:@\s*\$\s*({number})\s*)?\+\$\s*({number})", body)
            variable = match(rf"({number})\s*KWH\s*(?:@\s*\$\s*({number})\s*)?\+\$\s*({number})", body[fixed.end():])
            allowance, charge, used, variable_charge = map(_finite, (fixed[1], fixed[3], variable[1], variable[3]))
            rate = _finite(variable[2]) if variable[2] else variable_charge / used if used else 0.0
            segment_start, segment_end = iso(heading[1]), iso(heading[2])
            if segment_start > segment_end or not start <= segment_start <= segment_end <= end:
                raise ValueError
            segments.append({"start": segment_start, "end": segment_end, "kwh": allowance + used,
                             "fixed_kwh": allowance, "fixed": charge, "delivery_rate": rate})
        if abs(sum(s["kwh"] for s in segments) - kwh) > 0.01:
            raise ValueError
        # supply is one line, or one per segment when the supply price changed mid-cycle (each January)
        energies = list(re.finditer(rf"Energy Charge\s+({number})\s*KWH\s*@\s*\$\s*({number})\s*\+\$\s*({number})", text, re.I))
        if not 1 <= len(energies) <= 2 or abs(sum(_finite(e[1]) for e in energies) - kwh) > 0.01:
            raise ValueError
        energy = energies[-1]
        supply_rate = (energies[0][2] and _finite(energies[0][2])) if len(energies) == 1 else \
            sum(_finite(e[3]) for e in energies) / kwh        # kWh-weighted, so kWh x rate = the energy charges

        def tax(body):
            found = re.search(rf"Maine Sales Tax\s+\+?\$\s*({number})", body, re.I)
            return _finite(found[1]) if found else 0.0

        delivery_total, supply_total = _finite(delivery_end[1]), _finite(supply_end[1])
        return {"start": start, "end": end, "kwh": kwh, "amount": round(delivery_total + supply_total, 2),
                "bill_date": bill_date, "delivery_total": delivery_total, "supply_total": supply_total,
                "delivery_tax": tax(text[headings[0].start():delivery_end.start()]),
                "supply_tax": tax(text[energy.end():supply_end.start()]),
                "fixed_total": round(sum(s["fixed"] for s in segments), 2), "supply_rate": round(supply_rate, 6),
                "segments_json": json.dumps(segments), "source": "bill-pdf"}
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        raise BillingError("unsupported or incomplete CMP bill") from None


# pypdf diagnostics may quote document bytes. Silence them only in the importing context,
# leaving other threads' logging intact. Exceptions are also replaced with a constant error.
_private_pdf = ContextVar("private_billing_pdf", default=False)


class _PDFPrivacyFilter(logging.Filter):
    def filter(self, record):
        return not _private_pdf.get()


def parse_cmp_bill_pdf(data: bytes) -> dict:
    from pypdf import PdfReader

    # pypdf uses module loggers; filters on the parent logger do not filter descendants.
    # Some modules create their logger only on the first diagnostic, so include
    # imported modules as well as already-created loggers.
    for name in set(sys.modules) | set(logging.Logger.manager.loggerDict):
        if name == "pypdf" or name.startswith("pypdf."):
            logger = logging.getLogger(name)
            if not any(isinstance(f, _PDFPrivacyFilter) for f in logger.filters):
                logger.addFilter(_PDFPrivacyFilter())
    token = _private_pdf.set(True)
    try:
        reader = PdfReader(io.BytesIO(data))
        return parse_cmp_bill_text("\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception:
        raise BillingError("unsupported or unreadable CMP PDF") from None
    finally:
        _private_pdf.reset(token)


def _bill_check(bill: dict) -> dict | None:
    """Price a historical bill with its own fixed charges, allowances, and rates."""
    if not bill.get("segments_json") or bill.get("supply_rate") is None:
        return None
    segments = json.loads(bill["segments_json"])
    kwh = bill["kwh"]
    taxable = max(0.0, kwh - 750)
    delivery = sum(s["fixed"] + max(0.0, s["kwh"] - s["fixed_kwh"]) * s["delivery_rate"] for s in segments)
    delivery_tax = round(sum(taxable * s["kwh"] / kwh * s["delivery_rate"] * 0.055 for s in segments), 2)
    supply = round(kwh * bill["supply_rate"], 2)
    supply_tax = round(taxable * bill["supply_rate"] * 0.055, 2)
    total = round(round(delivery, 2) + delivery_tax + supply + supply_tax, 2)
    return {"model_total": total, "diff": round(bill["amount"] - total, 2)}


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
    # Sales tax, as CMP bills it: only kWh above the exemption, at their per-kWh delivery and supply
    # rates; the fixed charge isn't taxed (Sept 2026 bill: 600 kWh x (11.959 + 12.721)¢ x 5.5% = $8.15).
    exempt = settings["tax_exempt_kwh"] * months
    taxable = max(0.0, kwh - exempt)
    if p["tou"]:
        per_kwh_delivery = delivery / kwh if kwh else 0.0     # which kWh are "above 750" isn't defined by hour: average
    else:
        per_kwh_delivery = p["delivery"] if kwh > p["fixed_kwh"] * months else 0.0
    tax = taxable * (per_kwh_delivery + settings["supply_per_kwh"]) * settings["tax_rate"]
    return {"fixed": fixed, "delivery": delivery, "supply": supply, "tax": tax, "total": fixed + delivery + supply + tax}


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
            columns = {r[1] for r in db.execute("PRAGMA table_info(bills)")}
            for name, kind in BILL_COLUMNS.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE bills ADD COLUMN {name} {kind}")
            db.execute("""CREATE TABLE IF NOT EXISTS meter_hourly (
                ts INTEGER PRIMARY KEY, kwh REAL NOT NULL, temp_f REAL, delivered REAL, received REAL)""")

    def import_data(self, data: bytes) -> dict:
        if len(data) > MAX_IMPORT_BYTES:
            raise BillingError("upload exceeds 20 MiB")
        if data.startswith(b"%PDF-"):
            parsed = parse_cmp_bill_pdf(data)
            return {"kind": "bill-pdf", **self._save_bill(parsed)}
        try:
            rows = parse_meter_csv(data.decode("utf-8-sig"))
        except UnicodeError:
            raise BillingError("expected hourly meter CSV or CMP PDF") from None
        added = updated = 0
        with self._lock, self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                if db.execute("SELECT 1 FROM meter_hourly WHERE ts=?", (row[0],)).fetchone():
                    updated += 1
                else:
                    added += 1
                db.execute("""INSERT INTO meter_hourly VALUES (?,?,?,?,?) ON CONFLICT(ts) DO UPDATE SET
                    kwh=excluded.kwh, temp_f=excluded.temp_f, delivered=excluded.delivered, received=excluded.received""", row)
        return {"kind": "meter-csv", "added": added, "updated": updated,
                "from": min(r[0] for r in rows), "to": max(r[0] for r in rows)}

    def _save_bill(self, values: dict) -> dict:
        # Both call sites supply only whitelisted fields, never the user's entire object.
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
                    if type(v) is not int or not 1 <= v <= 31:
                        raise BillingError("billing_day must be a whole number from 1 to 31")
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
        s = self.settings()
        p = plan_for(s)
        values = {"start": start.isoformat(), "end": end.isoformat(), "kwh": float(kwh), "amount": float(amount),
                  "on_peak_kwh": float(on) if on is not None else None, "source": "manual"}
        if not p["tou"] or on is not None:
            months = bill_months(start, end)
            segment = {"start": start.isoformat(), "end": end.isoformat(), "kwh": kwh,
                       "fixed": p["fixed"] * months, "fixed_kwh": p["fixed_kwh"] * months,
                       "delivery_rate": p["delivery"]}
            if p["tou"]:
                segment.update(tou=p["tou"], delivery_rate=(on * p["tou"]["on"] + (kwh - on) * p["tou"]["off"]) / kwh)
            values.update(fixed_total=p["fixed"] * months, supply_rate=s["supply_per_kwh"], segments_json=json.dumps([
                segment]))
        return self._save_bill(values)

    def remove_bill(self, bill_id: int) -> None:
        with self._conn() as db:
            if not db.execute("DELETE FROM bills WHERE id=?", (bill_id,)).rowcount:
                raise KeyError(bill_id)

    def calibration(self) -> float | None:
        p = plan_for(self.settings())
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
        with self._conn() as db:
            return [dict(r) for r in db.execute("SELECT * FROM meter_hourly ORDER BY ts")]

    @staticmethod
    def _meter_days(rows: list[dict]) -> dict:
        days = {}
        tz = local_tz()
        for row in rows:
            day = datetime.fromtimestamp(row["ts"], tz).date()
            item = days.setdefault(day, {"kwh": 0.0, "temps": [], "hours": 0})
            item["kwh"] += row["kwh"]
            item["hours"] += 1
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
        c = s["custom"]
        # the published plans, plus "custom" only once it's been filled in (all zeros means unused)
        plan_ids = [pid for pid in PLANS if pid != "custom" or s["plan"] == "custom" or c["fixed"] or c["delivery"]]
        monthly = {}
        for r in window:
            dt = datetime.fromtimestamp(r["ts"], tz)
            item = monthly.setdefault(dt.strftime("%Y-%m"), {"kwh": 0.0, "on": 0.0})
            item["kwh"] += r["kwh"]
            if is_on_peak(dt, PLANS["cmp-a-tou"]["tou"]):
                item["on"] += r["kwh"]
        months = []
        d = start
        while d < end:
            key = d.strftime("%Y-%m")
            item = monthly.get(key, {"kwh": 0.0, "on": 0.0})
            kwh = max(0.0, item["kwh"])
            totals = {pid: estimate(s, kwh, item["on"], pid)["total"] for pid in plan_ids}
            months.append({"month": key, "kwh": round(kwh, 3), "on_peak_share": round(item["on"] / kwh, 5) if kwh else 0.0,
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
            sense, _ = self._daily_kwh(min(daily), max(daily) + timedelta(days=1), today - timedelta(days=1))
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
            bills[-1].update(effective_cents_per_kwh=round(b["amount"] / b["kwh"] * 100, 2), check=_bill_check(b))
        return {"meter": {"hours": len(rows), "from": rows[0]["ts"], "to": rows[-1]["ts"]} if rows else None,
                "sense_vs_meter": comparison, "plans_12mo": self._plans_12mo(rows, today),
                "weather": {"bins": [{"from_f": lo, "to_f": lo + 10, "days": len(values),
                                      "kwh_per_day": round(sum(values) / len(values), 3)} for lo, values in sorted(bins.items())]} if bins else None,
                "bills": bills}

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
        recent = [daily[d] for d in sorted(daily) if today - timedelta(days=7) <= d < today]
        per_day = (sum(recent) / len(recent)) if recent else (so_far / elapsed if elapsed > 0 else 0.0)
        # past days Sense has no number for still used power: fill them at the recent rate
        missing = [start + timedelta(days=k) for k in range((today - start).days) if start + timedelta(days=k) not in daily]
        meter = self._meter_rows()
        seasonal = self._seasonal_factor(meter, today, end)
        projected = so_far + per_day * len(missing) + per_day * max(0.0, days - elapsed) * (seasonal if seasonal is not None else 1.0)
        projection_basis = "last year's meter data" if seasonal is not None else "last 7 days"

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
        annual = self._plans_12mo(meter, today)
        basis = "12 months of meter data" if annual else "last 7 days"
        for pid in ("cmp-a", "cmp-a-tou", "cmp-et"):
            b = {"total": annual["totals"][pid]} if annual else bill(projected, pid)
            reference = annual["totals"][s["plan"]] if annual else current["total"] if current else None
            if b is not None:
                compare.append({"plan": pid, "label": PLANS[pid]["label"], "total": b["total"],
                                "delta": round(b["total"] - reference, 2) if reference is not None else None, "basis": basis})
        return {
            "start": start.isoformat(), "end": end.isoformat(), "days": days, "elapsed_days": round(elapsed, 2),
            "daily": [{"date": d.isoformat(), "kwh": round(daily[d], 3)} for d in sorted(daily)],
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
        use the one marginal rate. Hourly average watts stand in for each hour's kWh, so the hour in progress
        weighs as a full one: close enough for a live readout; the cycle bill uses Sense's hourly kWh."""
        r = self.rates()
        if not r.get("tou"):
            return kwh * r["marginal_cents_per_kwh"] / 100, r
        weights = [(h, w) for h, w in enumerate(hourly_w or []) if w]
        total = sum(w for _, w in weights)
        on = sum(w for h, w in weights if is_on_peak(datetime(day.year, day.month, day.day, h), r["tou"])) / total if total else 0.0
        return kwh * (on * r["marginal_on_cents"] + (1 - on) * r["marginal_off_cents"]) / 100, r


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")
