"""Energy history from Sense's cloud (days, months, years), cached in settings.db.

Senseless only has its own readings since it started; Sense keeps years. This proxies
`app/monitors/{id}/history/usage` (found in the Android client) and trims the answer to
what the History view draws.

Quirk: a `start` at local midnight returns the *previous* period, so requests anchor at
local noon of the asked date.
"""

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import requests

from . import auth
from .summary import local_tz

SCALES = {"day": "DAY", "month": "MONTH", "year": "YEAR"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENT_TTL_S = 600          # the period we're in keeps changing
RECENT_TTL_S = 6 * 3600      # yesterday / last month: Sense may still settle numbers


class HistoryError(ValueError):
    pass


def period_key(scale: str, d: date) -> str:
    return {"day": d.isoformat(), "month": d.strftime("%Y-%m"), "year": d.strftime("%Y")}[scale]


def period_contains_today(scale: str, d: date, today: date) -> bool:
    return period_key(scale, d) == period_key(scale, today)


def ttl_for(scale: str, d: date, today: date) -> float | None:
    """Seconds a cached period stays fresh; None = forever (a settled past period)."""
    if period_contains_today(scale, d, today):
        return CURRENT_TTL_S
    prev = {"day": today - timedelta(days=1), "month": (today.replace(day=1) - timedelta(days=1)),
            "year": today.replace(month=1, day=1) - timedelta(days=1)}[scale]
    if period_key(scale, d) == period_key(scale, prev):
        return RECENT_TTL_S
    return None


def trim(scale: str, raw: dict) -> dict:
    c = raw.get("consumption") or {}
    devices = []
    for dev in raw.get("device_breakdown") or []:
        dc = dev.get("consumption") or {}
        kwh = dc.get("usage_total_kwh") or 0
        if kwh <= 0:
            continue
        devices.append({"id": dev.get("id"), "name": dev.get("name"), "icon": dev.get("icon"), "kwh": round(kwh, 3),
                        "cost_cents": dc.get("cost_total_cents"), "pct": dc.get("percentage")})
    devices.sort(key=lambda x: -x["kwh"])
    rnd = lambda xs: [round(x, 3) if x is not None else None for x in xs or []]
    return {"scale": scale, "start": raw.get("start"), "kwh_total": round(c.get("usage_total_kwh") or 0, 3),
            "cost_total_cents": c.get("cost_total_cents"), "kwh": rnd(c.get("usage_breakdown_kwh")),
            "cost_cents": c.get("cost_breakdown_cents") or [], "devices": devices}


class History:
    def __init__(self, settings_db: Path, session_getter, fetch=None):
        self.db_path = settings_db
        self.session = session_getter
        self.fetch = fetch or self._fetch_sense
        with self._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS history_cache (
                scale TEXT NOT NULL, period TEXT NOT NULL, fetched_at REAL NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (scale, period))""")

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _fetch_sense(self, scale: str, d: date) -> dict:
        noon = datetime.combine(d, dtime(12, 0), tzinfo=local_tz()).astimezone(timezone.utc)
        params = {"scale": SCALES[scale], "start": noon.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        for attempt in (0, 1):
            s = self.session() or auth.session()
            r = requests.get(f"{auth.API}/app/monitors/{s.monitor_id}/history/usage", params=params, headers=s.headers(), timeout=20)
            if r.status_code in (401, 403) and attempt == 0:
                auth.session(fresh=True)
                continue
            r.raise_for_status()
            return r.json()
        raise HistoryError("Sense rejected the history request")

    def get(self, scale: str, date_str: str, today: date | None = None) -> dict:
        if scale not in SCALES:
            raise HistoryError("scale must be day, month or year")
        if not DATE_RE.match(date_str or ""):
            raise HistoryError("date must be YYYY-MM-DD")
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            raise HistoryError("date must be a real date")
        today = today or datetime.now(local_tz()).date()
        if d > today:
            raise HistoryError("that's in the future")
        key = period_key(scale, d)
        with self._conn() as db:
            row = db.execute("SELECT fetched_at, body FROM history_cache WHERE scale=? AND period=?", (scale, key)).fetchone()
        ttl = ttl_for(scale, d, today)
        if row and (ttl is None or time.time() - row[0] < ttl):
            return {**json.loads(row[1]), "cached": True, "period": key}
        try:
            body = trim(scale, self.fetch(scale, d))
        except Exception as e:
            if row:  # Sense unreachable: serve the stale copy rather than nothing
                return {**json.loads(row[1]), "cached": True, "stale": True, "period": key}
            raise HistoryError(f"couldn't fetch history from Sense ({type(e).__name__})")
        with self._conn() as db:
            db.execute("INSERT OR REPLACE INTO history_cache VALUES (?,?,?,?)", (scale, key, time.time(), json.dumps(body)))
        return {**body, "cached": False, "period": key}
