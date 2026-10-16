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


def series(db_path: Path, minutes: float, bucket_s: float) -> list[dict]:
    """Per-leg power averaged into fixed buckets, for charts (one row per bucket with data)."""
    since = time.time() - minutes * 60
    db = _connect(db_path)
    try:
        rows = db.execute(
            "SELECT CAST(ts / ? AS INTEGER) * ? AS t, avg(w) AS w, avg(l1_w) AS l1, avg(l2_w) AS l2 "
            "FROM readings WHERE ts >= ? GROUP BY CAST(ts / ? AS INTEGER) ORDER BY t",
            (bucket_s, bucket_s, since, bucket_s),
        ).fetchall()
    finally:
        db.close()
    return [{"t": r["t"], "w": round(r["w"], 1) if r["w"] is not None else None,
             "l1": round(r["l1"], 1) if r["l1"] is not None else None,
             "l2": round(r["l2"], 1) if r["l2"] is not None else None} for r in rows]
