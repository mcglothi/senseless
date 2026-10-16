"""Read-side queries over the readings table: today's energy summary and downsampled series."""

import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Gaps longer than this (feed outage) don't count as energy at the last known power.
MAX_GAP_S = 10.0


def local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("SENSELESS_TZ") or os.environ.get("TZ") or "America/New_York")


def _connect(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def day_bounds(now: float | None = None, tz: ZoneInfo | None = None) -> tuple[float, float]:
    tz = tz or local_tz()
    dt = datetime.fromtimestamp(now if now is not None else time.time(), tz)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def today(db_path: Path, cost_cents_per_kwh: float | None, now: float | None = None, tz: ZoneInfo | None = None) -> dict:
    """Energy since local midnight: kWh (trapezoid over readings, outage gaps excluded), peak, avg, hourly avgs."""
    tz = tz or local_tz()
    now = now if now is not None else time.time()
    start, _ = day_bounds(now, tz)
    db = _connect(db_path)
    try:
        rows = db.execute("SELECT ts, w FROM readings WHERE ts >= ? AND w IS NOT NULL ORDER BY ts", (start,)).fetchall()
    finally:
        db.close()
    wh = 0.0
    covered = 0.0
    hourly_sum = [0.0] * 24
    hourly_n = [0] * 24
    peak = None
    prev = None
    for r in rows:
        ts, w = r["ts"], r["w"]
        h = datetime.fromtimestamp(ts, tz).hour
        hourly_sum[h] += w
        hourly_n[h] += 1
        peak = w if peak is None or w > peak else peak
        if prev is not None:
            dt = ts - prev[0]
            if 0 < dt <= MAX_GAP_S:
                wh += (w + prev[1]) / 2 * dt / 3600
                covered += dt
        prev = (ts, w)
    kwh = wh / 1000
    hourly = [round(hourly_sum[i] / hourly_n[i]) if hourly_n[i] else None for i in range(24)]
    return {
        "since": start,
        "kwh": round(kwh, 3),
        "cost": round(kwh * cost_cents_per_kwh / 100, 2) if cost_cents_per_kwh else None,
        "cost_cents_per_kwh": cost_cents_per_kwh,
        "peak_w": round(peak) if peak is not None else None,
        "avg_w": round(wh / covered * 3600) if covered else None,
        "coverage": round(covered / max(1.0, now - start), 3),  # share of the day we actually have data for
        "hourly_w": hourly,
    }


def baseline(db_path: Path, hours: float = 24, pct: float = 0.05, now: float | None = None) -> dict:
    """Always-on load measured by Senseless: each leg's low percentile over the window (robust to noise
    and to brief dips, unlike a minimum). Total = sum of the legs' baselines."""
    now = now if now is not None else time.time()
    db = _connect(db_path)
    try:
        rows = db.execute("SELECT l1_w, l2_w FROM readings WHERE ts >= ? AND l1_w IS NOT NULL AND l2_w IS NOT NULL",
                          (now - hours * 3600,)).fetchall()
    finally:
        db.close()
    if len(rows) < 120:  # under a minute of data: not meaningful yet
        return {"hours": hours, "samples": len(rows), "l1_w": None, "l2_w": None, "w": None}
    q = lambda xs: sorted(xs)[int(pct * (len(xs) - 1))]
    l1, l2 = q([r["l1_w"] for r in rows]), q([r["l2_w"] for r in rows])
    return {"hours": hours, "samples": len(rows), "pct": pct, "l1_w": round(l1, 1), "l2_w": round(l2, 1), "w": round(l1 + l2, 1)}


def series(db_path: Path, start: float, end: float, bucket_s: float) -> list[dict]:
    """Per-leg power averaged into fixed buckets over [start, end), for charts (one row per bucket with data).
    Also min/max per bucket, so zoomed-out charts still show short spikes."""
    db = _connect(db_path)
    try:
        rows = db.execute(
            "SELECT CAST(ts / ? AS INTEGER) * ? AS t, avg(w) AS w, avg(l1_w) AS l1, avg(l2_w) AS l2, max(w) AS w_max "
            "FROM readings WHERE ts >= ? AND ts < ? GROUP BY CAST(ts / ? AS INTEGER) ORDER BY t",
            (bucket_s, bucket_s, start, end, bucket_s),
        ).fetchall()
    finally:
        db.close()
    rnd = lambda v: round(v, 1) if v is not None else None
    return [{"t": r["t"], "w": rnd(r["w"]), "l1": rnd(r["l1"]), "l2": rnd(r["l2"]), "w_max": rnd(r["w_max"])} for r in rows]


def local_days(db_path: Path, start: float, end: float, now: float | None = None, tz: ZoneInfo | None = None) -> dict:
    """Energy per local day from Senseless's own readings: {date: {kwh, coverage, hourly: [kWh x 24]}}.
    Per-minute means from SQL; a minute with readings counts as covered. Coverage is covered minutes over the
    day's minutes (today: minutes elapsed so far), so callers can tell a complete day from a gap."""
    tz = tz or local_tz()
    now = now if now is not None else time.time()
    db = _connect(db_path)
    try:
        rows = db.execute("SELECT CAST(ts / 60 AS INTEGER) AS m, avg(w) AS w FROM readings "
                          "WHERE ts >= ? AND ts < ? AND w IS NOT NULL GROUP BY m", (start, min(end, now))).fetchall()
    finally:
        db.close()
    out: dict = {}
    for r in rows:
        dt = datetime.fromtimestamp(r["m"] * 60, tz)
        item = out.setdefault(dt.date(), {"wh": 0.0, "minutes": 0, "hourly_wh": [0.0] * 24})
        wh = max(0.0, r["w"]) / 60
        item["wh"] += wh
        item["minutes"] += 1
        item["hourly_wh"][dt.hour] += wh
    result = {}
    for day, item in out.items():
        begin = datetime.combine(day, datetime.min.time(), tz).timestamp()
        span = min(now, datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).timestamp()) - begin
        result[day] = {"kwh": item["wh"] / 1000, "coverage": min(1.0, item["minutes"] * 60 / span) if span > 0 else 0.0,
                       "hourly": [w / 1000 for w in item["hourly_wh"]]}
    return result
