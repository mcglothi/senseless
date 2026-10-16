import asyncio
from datetime import date

import pytest
from aiohttp.test_utils import TestClient, TestServer

from senseless import web
from senseless.history import History, HistoryError, ttl_for, trim
from senseless.state import LiveState
from senseless.store import Store

RAW = {"start": "2026-10-01T05:00:00Z",
       "consumption": {"usage_total_kwh": 604.97, "cost_total_cents": 7163, "usage_breakdown_kwh": [41.09, 41.56], "cost_breakdown_cents": [487, 492]},
       "device_breakdown": [
           {"id": "a", "name": "Coffee Maker", "icon": "cup", "consumption": {"usage_total_kwh": 0.0}},
           {"id": "b", "name": "Well pump", "icon": "pump", "consumption": {"usage_total_kwh": 12.5, "cost_total_cents": 148, "percentage": 2}},
           {"id": "c", "name": "Always On", "icon": "alwayson", "consumption": {"usage_total_kwh": 400.1, "cost_total_cents": 4737, "percentage": 66}}]}


def test_trim_keeps_totals_and_sorts_devices():
    t = trim("month", RAW)
    assert t["kwh_total"] == 604.97 and t["cost_total_cents"] == 7163 and t["kwh"] == [41.09, 41.56]
    assert [d["name"] for d in t["devices"]] == ["Always On", "Well pump"]  # zero-use dropped, biggest first


def test_ttl_current_recent_and_settled():
    today = date(2026, 10, 16)
    assert ttl_for("day", today, today) == 600
    assert ttl_for("day", date(2026, 10, 15), today) == 6 * 3600
    assert ttl_for("day", date(2026, 10, 1), today) is None
    assert ttl_for("month", date(2026, 10, 3), today) == 600
    assert ttl_for("month", date(2026, 9, 3), today) == 6 * 3600
    assert ttl_for("month", date(2026, 7, 3), today) is None


def test_cache_validation_and_stale_fallback(tmp_path):
    calls = []

    def fetch(scale, d):
        calls.append((scale, d))
        return RAW

    h = History(tmp_path / "s.db", lambda: None, fetch=fetch)
    today = date(2026, 10, 16)
    a = h.get("month", "2026-07-04", today=today)
    b = h.get("month", "2026-07-20", today=today)          # same period -> cache
    assert len(calls) == 1 and a["cached"] is False and b["cached"] is True and b["period"] == "2026-07"
    for bad in (("week", "2026-07-04"), ("month", "2026-7-4"), ("month", "2026-02-30"), ("day", "2026-10-17")):
        with pytest.raises(HistoryError):
            h.get(*bad, today=today)
    # current period refetches after TTL; if Sense is down, the stale copy is served
    h.get("day", "2026-10-16", today=today)
    import sqlite3
    with sqlite3.connect(tmp_path / "s.db") as db:
        db.execute("UPDATE history_cache SET fetched_at = fetched_at - 3600")

    def down(scale, d):
        raise OSError("offline")

    h.fetch = down
    r = h.get("day", "2026-10-16", today=today)
    assert r["stale"] is True and r["kwh_total"] == 604.97


def test_history_endpoint(tmp_path):
    async def run():
        Store(tmp_path / "t.db").close()
        h = History(tmp_path / "s.db", lambda: None, fetch=lambda s, d: RAW)
        async with TestClient(TestServer(web.make_app(LiveState(), tmp_path / "t.db", history=h))) as c:
            r = await c.get("/api/history?scale=month&date=2026-07-04")
            assert r.status == 200 and (await r.json())["devices"][0]["name"] == "Always On"
            assert (await c.get("/api/history?scale=hour&date=2026-07-04")).status == 400
            assert (await c.get("/api/history?scale=day&date=9999-01-01")).status == 400
    asyncio.run(run())
