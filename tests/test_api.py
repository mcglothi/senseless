import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from senseless import summary, web
from senseless.appearance import Appearance, AppearanceError, parse_scheme
from senseless.state import LiveState
from senseless.store import Store

SCHEME = """system: "base16"
name: "Gruvbox dark, medium"
author: "Dawid Kurek"
variant: "dark"
palette:
  base00: "#282828" # ----
  base01: "#3c3836"
  base02: "#504945"
  base03: "#665c54"
  base04: "#bdae93"
  base05: "#d5c4a1"
  base06: "#ebdbb2"
  base07: "#fbf1c7"
  base08: "#fb4934"
  base09: "#fe8019"
  base0A: "#fabd2f"
  base0B: "#b8bb26"
  base0C: "#8ec07c"
  base0D: "#83a598"
  base0E: "#d3869b"
  base0F: "#d65d0e"
"""


def fake_gallery():
    s = parse_scheme(SCHEME)
    s["id"] = "gruvbox-dark-medium"
    return [s]


def test_parse_scheme():
    s = parse_scheme(SCHEME)
    assert s["name"] == "Gruvbox dark, medium" and s["variant"] == "dark"
    assert s["palette"]["base0A"] == "#fabd2f" and len(s["palette"]) == 16
    assert parse_scheme(SCHEME.replace('  base0F: "#d65d0e"\n', "")) is None  # incomplete palette rejected


def test_appearance_validation_and_install(tmp_path):
    ap = Appearance(tmp_path / "t.db", tmp_path, fetch=fake_gallery)
    assert ap.settings()["dark"] == "senseless"
    assert [t["id"] for t in ap.themes()] == ["senseless", "senseless-light"]
    with pytest.raises(AppearanceError):
        ap.update({"radius": 99})
    with pytest.raises(AppearanceError):
        ap.update({"dark": "gruvbox-dark-medium"})  # not installed yet
    with pytest.raises(AppearanceError):
        ap.update({"bogus": 1})
    ap.install("gruvbox-dark-medium")
    assert ap.update({"dark": "gruvbox-dark-medium", "accent": "base0B"})["dark"] == "gruvbox-dark-medium"
    # removing the theme in use falls back to the default
    ap.remove("gruvbox-dark-medium")
    assert ap.settings()["dark"] == "senseless"
    with pytest.raises(AppearanceError):
        ap.remove("senseless")


def test_gallery_serves_stale_cache_when_offline(tmp_path):
    ap = Appearance(tmp_path / "t.db", tmp_path, fetch=fake_gallery)
    assert len(ap.gallery()["themes"]) == 1

    def boom():
        raise OSError("offline")

    ap.fetch = boom
    assert "error" not in ap.gallery(refresh=True)       # refresh within REFRESH_MIN_S: served from cache, no fetch
    c = json.loads(ap.cache.read_text())
    c["fetched_at"] -= 3600                               # an hour later the refresh is allowed
    ap.cache.write_text(json.dumps(c))
    g = ap.gallery(refresh=True)
    assert len(g["themes"]) == 1 and "cached copy" in g["error"]


def test_gallery_refresh_is_throttled(tmp_path):
    calls = []

    def counting():
        calls.append(1)
        return fake_gallery()

    ap = Appearance(tmp_path / "t.db", tmp_path, fetch=counting)
    for _ in range(5):
        ap.gallery(refresh=True)
    assert len(calls) == 1


def _reading(store, ts, w, frame):
    store.realtime({"frame": frame, "epoch": ts, "w": w, "channels": [w / 2, w / 2], "voltage": [120, 120], "hz": 60}, ts)


def test_today_energy_skips_outage_gaps(tmp_path):
    tz = ZoneInfo("America/New_York")
    start = datetime(2026, 10, 15, 0, 0, tzinfo=tz).timestamp()
    s = Store(tmp_path / "t.db")
    frame = 1000
    # 1 kW for one hour at 1 s spacing -> 1 kWh
    for i in range(3601):
        _reading(s, start + i, 1000.0, frame)
        frame += 60
    # a 2 h outage, then 2 kW for 10 s: the gap must not count as energy
    for i in range(11):
        _reading(s, start + 3600 + 7200 + i, 2000.0, frame)
        frame += 60
    s.commit()
    r = summary.today(tmp_path / "t.db", 12.0, now=start + 3 * 3600 + 20, tz=tz)
    assert r["kwh"] == pytest.approx(1.0 + 2000 * 10 / 3600 / 1000, abs=1e-3)
    assert r["peak_w"] == 2000 and r["hourly_w"][0] == 1000 and r["hourly_w"][3] == 2000 and r["hourly_w"][2] is None
    assert r["cost"] == pytest.approx(0.12, abs=0.01)
    assert 0.3 < r["coverage"] < 0.4


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_stream_delivers_snapshot_then_events(tmp_path):
    async def run():
        st = LiveState()
        Store(tmp_path / "t.db").close()
        app = web.make_app(st, tmp_path / "t.db", Appearance(tmp_path / "t.db", tmp_path, fetch=fake_gallery))
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/api/stream")
            assert r.headers["Content-Type"].startswith("text/event-stream")
            first = await r.content.readuntil(b"\n\n")
            assert first.startswith(b"event: snapshot")
            for _ in range(50):  # wait for the handler to subscribe
                if st.subscribers:
                    break
                await asyncio.sleep(0.01)
            st.publish({"type": "rt", "w": 1234})
            ev = await r.content.readuntil(b"\n\n")
            assert json.loads(ev.decode().removeprefix("data: "))["w"] == 1234
            r.close()
            # appearance routes round-trip
            assert (await (await c.post("/api/appearance", json={"radius": 16})).json())["radius"] == 16
            assert (await c.post("/api/appearance", json={"radius": 99})).status == 400
            assert len((await (await c.get("/api/themes")).json())["themes"]) == 2
            # install runs on a worker thread: must not trip SQLite's same-thread check
            r = await c.post("/api/themes/install", json={"id": "gruvbox-dark-medium"})
            assert r.status == 200, await r.text()
            assert [t["id"] for t in (await r.json())["themes"]][-1] == "gruvbox-dark-medium"
            assert (await c.delete("/api/themes/gruvbox-dark-medium")).status == 200
            # cross-site writes are refused: text/plain simple POSTs and foreign Origins
            assert (await c.post("/api/appearance", data='{"radius": 10}', headers={"Content-Type": "text/plain"})).status == 415
            assert (await c.post("/api/appearance", json={"radius": 10}, headers={"Origin": "http://evil.example"})).status == 403
            assert (await c.delete("/api/themes/x", headers={"Origin": "http://evil.example"})).status == 403
            # every core route is registered (a stray comment once dropped /healthz and /metrics)
            for path in ("/healthz", "/metrics", "/api/status", "/api/today", "/api/series", "/api/deltas", "/status"):
                assert (await c.get(path)).status in (200, 503), path
            # client-side routes don't 404 on reload
            for path in ("/timeline", "/loads", "/history"):
                assert (await c.get(path)).status == 200
    asyncio.run(run())


def test_appearance_saves_while_collector_holds_a_write_transaction(tmp_path):
    # The collector keeps an open write transaction on senseless.db between its 5 s commits.
    s = Store(tmp_path / "senseless.db")
    _reading(s, time.time(), 1000.0, 1)  # uncommitted insert -> write lock held
    ap = Appearance(tmp_path / "settings.db", tmp_path, fetch=fake_gallery)
    assert ap.update({"mode": "light"})["mode"] == "light"
    s.close()


def test_shutdown_is_prompt_with_an_open_stream(tmp_path, monkeypatch):
    import aiohttp
    monkeypatch.setenv("SENSELESS_HTTP_PORT", "18791")
    monkeypatch.setenv("SENSELESS_HTTP_HOST", "127.0.0.1")

    async def run():
        st = LiveState()
        Store(tmp_path / "t.db").close()
        runner = await web.serve(st, tmp_path / "t.db")
        async with aiohttp.ClientSession() as s:
            r = await s.get("http://127.0.0.1:18791/api/stream")
            await r.content.readuntil(b"\n\n")  # snapshot: the handler is live
            t0 = time.monotonic()
            st.close_streams()
            await runner.cleanup()
            return time.monotonic() - t0
    assert asyncio.run(run()) < 2.0


def test_series_and_deltas_accept_windows(tmp_path):
    s = Store(tmp_path / "t.db")
    t0 = 1_800_000_000.0
    for i in range(120):  # 2 minutes at 1 Hz, with a 5 kW spike at 60 s
        w = 5000.0 if i == 60 else 1000.0
        s.realtime({"frame": 1000 + i * 60, "epoch": t0 + i, "w": w, "channels": [w / 2, w / 2], "voltage": [120, 120], "hz": 60,
                    "deltas": [{"start_frame": 1000 + i * 60, "frame": 1000 + i * 60, "channel": 0, "w": 40}] if i % 30 == 0 else []}, t0 + i)
    s.close()

    async def run():
        app = web.make_app(LiveState(), tmp_path / "t.db")
        async with TestClient(TestServer(app)) as c:
            rows = await (await c.get(f"/api/series?start={t0}&end={t0 + 120}&bucket=60")).json()
            assert [r["t"] for r in rows] == [t0, t0 + 60]
            assert rows[1]["w_max"] == 5000 and rows[1]["w"] < 2000   # spike survives the bucket average
            d = await (await c.get(f"/api/deltas?start={t0}&end={t0 + 61}")).json()
            assert len(d) == 3                                          # i = 0, 30, 60
            assert (await c.get("/api/series?start=abc")).status == 400
            assert (await c.get(f"/api/series?start={t0 + 10}&end={t0}")).status == 400
            # a huge window with a tiny bucket is coarsened to stay chart-sized
            rows = await (await c.get(f"/api/series?start={t0 - 86400}&end={t0 + 120}&bucket=1")).json()
            assert len(rows) <= 5000
    asyncio.run(run())
