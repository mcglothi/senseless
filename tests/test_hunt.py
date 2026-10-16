import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from senseless import web
from senseless.circuits import Circuits
from senseless.detect import Patterns
from senseless.floorplans import Floorplans
from senseless.hunt import Hunt, HuntError, breaker_drop, name_ideas
from senseless.state import LiveState


class FakeDetector:
    def __init__(self, db):
        self.patterns = Patterns(db)


class Sim:
    """Drives a Hunt with half-second readings, like the live feed."""

    def __init__(self, hunt, l1=500.0, l2=800.0):
        self.h, self.t, self.l1, self.l2 = hunt, 1000.0, l1, l2
        self.frame = 0

    def tick(self, n=1, deltas=(), devices=()):
        for i in range(n):
            self.t += 0.5
            self.frame += 30
            ds = [dict(d, start_frame=self.frame + d.get("df", 0)) for d in deltas] if i == 0 else []
            self.h.feed(self.t, [self.l1, self.l2], ds, devices)

    def step(self, leg, w, df=0):
        """A load switching: leg level changes and the monitor reports the step."""
        if leg == 1:
            self.l1 += w
        else:
            self.l2 += w
        return {"channel": leg - 1, "w": w, "df": df}

    def settle(self):
        self.tick(10)   # 5 s > SETTLE_S


def make(tmp_path):
    h = Hunt(Circuits(tmp_path / "settings.db"), FakeDetector(tmp_path / "settings.db"))
    s = Sim(h)
    s.tick(8)
    return h, s


def test_device_cycle_measure_and_label(tmp_path):
    h, s = make(tmp_path)
    assert h.view()["actions"] == ["start"]
    v = h.start("device")
    assert v["phase"] == "settling"
    s.settle()
    assert h.phase == "ready" and "turn it on" in h.view()["prompt"]
    s.tick(deltas=[s.step(1, 1210)])
    assert h.phase == "ready" and h.view()["capturing"]      # waits out the capture window
    s.tick(6)
    v = h.view()
    assert h.phase == "on" and v["on"]["legs"] == "L1" and v["on"]["w"] == 1210 and v["on"]["level_change"] == pytest.approx(1210)
    assert "Now turn it off" in v["prompt"] and "label" in v["actions"]
    s.tick(20)
    s.tick(deltas=[s.step(1, -1190)])
    s.tick(6)
    m = h.view()["measurement"]
    assert h.phase == "measured" and m["cycles"] == 1 and m["off_sizes"] == [1190] and m["durations_s"][0] == pytest.approx(13.5)
    assert "Turn it on and off once more" in h.view()["prompt"]
    # second cycle confirms
    s.tick(deltas=[s.step(1, 1205)])
    s.tick(6)
    s.tick(deltas=[s.step(1, -1205)])
    s.tick(6)
    m = h.view()["measurement"]
    assert m["cycles"] == 2 and m["consistent"] and "The cycles match" in h.view()["prompt"]
    assert m["ideas"][0] == "Kettle"
    out = h.label("Kettle")
    assert out["pattern"]["name"] == "Kettle" and out["pattern"]["legs"] == "L1" and out["pattern"]["center_w"] == pytest.approx(1207.5)
    assert h.phase == "ready" and h.view()["prompt"].startswith("Saved Kettle.")
    # a later hunt of the same device finds the named pattern
    s.tick(deltas=[s.step(1, 1200)])
    s.tick(6)
    match = h.view()["measurement"]["matches"][0]
    assert match["name"] == "Kettle" and match["same_pattern"] and "looks like Kettle" in h.view()["prompt"]


def test_device_240v_and_multistage(tmp_path):
    h, s = make(tmp_path)
    h.start("device")
    s.settle()
    s.tick(deltas=[s.step(1, 2400), s.step(2, 2350)])
    s.tick(6)
    assert h.view()["on"]["legs"] == "both" and h.view()["on"]["w"] == 4750
    h.retry()
    assert h.phase == "ready" and h.view()["measurement"] is None
    # microwave: +300 then +1700 a second and a half later = one switch, two stages
    s.tick(deltas=[s.step(1, 300)])
    s.tick(2)
    s.tick(deltas=[s.step(1, 1700)])
    s.tick(6)
    on = h.view()["on"]
    assert on["w"] == 2000 and on["stages"] == 2


def test_device_ignores_other_loads(tmp_path):
    h, s = make(tmp_path)
    h.start("device")
    s.settle()
    s.tick(deltas=[s.step(1, 500)])
    s.tick(6)
    # fridge on the other leg switching off, and a mismatched off on ours: neither closes the cycle
    s.tick(deltas=[s.step(2, -150)])
    s.tick(6)
    s.tick(deltas=[s.step(1, -120)])
    s.tick(6)
    assert h.phase == "on" and h.noise >= 2 and "doesn't match" in h.view()["prompt"]
    s.tick(deltas=[s.step(1, -480)])
    s.tick(6)
    assert h.phase == "measured"
    # while waiting for a repeat, a different-size load starting isn't folded in
    s.tick(deltas=[s.step(1, 90)])
    s.tick(6)
    assert h.phase == "measured" and "isn't this device" in h.view()["prompt"]
    # steps below the detector's floor are ignored entirely
    s.tick(deltas=[s.step(1, 12)])
    s.tick(6)
    assert h.view()["measurement"]["cycles"] == 1


def test_steps_in_closing_update_are_kept(tmp_path):
    """An off arriving in the very update that closes the on's capture window must not be lost."""
    h, s = make(tmp_path)
    h.start("device")
    s.settle()
    s.tick(deltas=[s.step(2, 200)])
    s.tick(4)                                   # 2 s: window still open
    s.tick(deltas=[s.step(2, -200)])            # 2.5 s: closes the on, and is itself the off
    s.tick(6)
    assert h.phase == "measured" and h.view()["measurement"]["off_sizes"] == [200]


def test_sense_guess(tmp_path):
    h, s = make(tmp_path)
    h.start("device")
    s.settle()
    dev = [{"name": "Microwave", "w": 0}, {"name": "Always On", "w": 900}]
    s.tick(deltas=[s.step(1, 1500)], devices=dev)
    s.tick(6, devices=dev)
    s.tick(4, devices=[{"name": "Microwave", "w": 1450}, {"name": "Always On", "w": 2400}])
    assert h.view()["measurement"]["sense_guess"] == ["Microwave"]
    assert "Sense thinks it might be Microwave" in h.view()["prompt"] or h.phase == "on"


def test_breaker_mode(tmp_path):
    h, s = make(tmp_path)
    h.start("breaker")
    s.settle()
    assert h.phase == "watching" and h.view()["drop"]["meaningful"] is False
    with pytest.raises(HuntError):
        h.save("14", "Basement")
    s.tick(deltas=[s.step(2, -612)])
    s.tick(6)
    d = h.view()["drop"]
    assert d["l2"] == -612 and d["total"] == -612 and "Which breaker" in h.view()["prompt"]
    saved = h.save("14", "Basement")
    assert saved["l2_w"] == -612 and h.phase == "flipback" and "Switch it back on" in h.view()["prompt"]
    assert h.circuits.all()[0]["breaker"] == "14"


def test_breaker_drop_port_matches_js():
    b = {"t": 10, "l1": 500, "l2": 800}
    # missed step: measured change wins
    assert breaker_drop(b, {"l1": 500, "l2": 200}, [])["l2"] == -600
    # caught -1000 step plus a +400 start must not read as +400
    d = breaker_drop(b, {"l1": 500, "l2": 200}, [{"t": 11, "leg": 2, "w": 400}])
    assert d["l2"] == -600
    # a step that arrived with the baseline belongs to it
    assert breaker_drop(b, {"l1": 500, "l2": 800}, [{"t": 10, "leg": 1, "w": -300}])["meaningful"] is False


def test_commands_need_live_feed(tmp_path):
    h = Hunt(healthy=lambda: False)
    with pytest.raises(HuntError):
        h.start("device")
    h = Hunt()
    with pytest.raises(HuntError):
        h.start("device")                       # no readings yet
    with pytest.raises(HuntError):
        h.retry()


def test_name_ideas():
    assert name_ideas(1500, "L1", True, 60)[0] == "Microwave"
    assert name_ideas(4000, "both", False, 600)[0] == "Electric range"


def test_hunt_api_long_poll_and_label_to_room(tmp_path):
    async def go():
        st = LiveState()
        events = []
        st.publish = events.append
        fp = Floorplans(tmp_path / "settings.db", tmp_path)
        h = Hunt(Circuits(tmp_path / "settings.db"), FakeDetector(tmp_path / "settings.db"), publish=st.publish)
        floor = fp.add_floor({"name": "Main"})
        room = fp.add_room(floor["id"], {"name": "Kitchen", "x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4})
        sim = Sim(h)
        sim.tick(8)
        app = web.make_app(st, tmp_path / "t.db", hunt=h, floorplans=fp)
        async with TestClient(TestServer(app)) as c:
            v = await (await c.get("/api/hunt")).json()
            assert v["phase"] == "idle" and v["actions"] == ["start"]
            assert (await c.post("/api/hunt/start", json={"mode": "bogus"})).status == 409
            assert (await c.post("/api/hunt/start", json={"mode": "device"}, headers={"Origin": "http://evil.example"})).status == 403
            v = await (await c.post("/api/hunt/start", json={"mode": "device"})).json()
            assert v["phase"] == "settling"
            # long-poll wakes when the session changes
            poll = asyncio.ensure_future(c.get(f"/api/hunt?wait={v['version']}&timeout=5"))
            await asyncio.sleep(0.05)
            assert not poll.done()
            sim.settle()
            r = await asyncio.wait_for(poll, 2)
            assert (await r.json())["phase"] == "ready"
            # nothing measured yet: label refused with the session attached
            r = await c.post("/api/hunt/label", json={"name": "Microwave"})
            assert r.status == 409 and (await r.json())["hunt"]["phase"] == "ready"
            sim.tick(deltas=[sim.step(1, 1500)])
            sim.tick(6)
            assert (await c.post("/api/hunt/label", json={"name": "Microwave", "room_id": 999})).status == 409
            r = await (await c.post("/api/hunt/label", json={"name": "Microwave", "room_id": room["id"]})).json()
            assert r["pattern"]["name"] == "Microwave" and r["marker"]["ref"] == "Microwave" and r["marker"]["kind"] == "load"
            assert 0.2 <= r["marker"]["x"] <= 0.6 and 0.2 <= r["marker"]["y"] <= 0.6
            assert r["hunt"]["phase"] == "ready"
            # empty-body commands work (voice clients), timeouts return the unchanged session
            v = await (await c.post("/api/hunt/stop")).json()
            assert v["phase"] == "idle"
            r = await c.get(f"/api/hunt?wait={v['version']}&timeout=0.1")
            assert (await r.json())["version"] == v["version"]
            assert (await c.get("/api/hunt?wait=x")).status == 400
            idx = await (await c.get("/api")).json()
            paths = {e["path"] for e in idx["endpoints"]}
            assert "/api/hunt/label" in paths and "/api/floors/{id}/markers" in paths
        assert any(e["type"] == "hunt" for e in events)
    asyncio.run(go())


def test_api_index_covers_every_route(tmp_path):
    """Every /api route the app serves is described in GET /api (so agents can find it)."""
    app = web.make_app(LiveState(), tmp_path / "t.db")
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()
              if r.resource is not None and r.resource.canonical.startswith("/api") and r.method != "HEAD"}
    described = {(e["method"], e["path"].split("?")[0].split("[")[0]) for e in web.API_INDEX}
    missing = {r for r in routes if r not in described and r != ("GET", "/api")}
    assert not missing, sorted(missing)


def test_breaker_drop_more_js_cases():
    """Cases carried over from the old web/src/ui/hunt.test.js (huntDrop moved server-side)."""
    # exact steps beat the noisier measured change; -2 W on L1 is noise
    d = breaker_drop({"t": 100, "l1": 90, "l2": 1030}, {"l1": 88, "l2": 425},
                     [{"t": 105, "leg": 2, "w": -612.4}, {"t": 50, "leg": 2, "w": -40}])
    assert d["l2"] == pytest.approx(-612.4) and d["l1"] == 0 and d["meaningful"] and not d["both"]
    # 240 V breaker: balanced drop on both legs
    d = breaker_drop({"t": 0, "l1": 1300, "l2": 1300}, {"l1": 80, "l2": 70},
                     [{"t": 1, "leg": 1, "w": -1217}, {"t": 1, "leg": 2, "w": -1232}])
    assert d["both"] and d["total"] == pytest.approx(-2449)


class ChattyDetector(FakeDetector):
    def loads(self, _day_start):
        return [{"legs": "L2", "center_w": 36.0, "runs_window": 300, "median_duration_s": 20, "name": None},
                {"legs": "L1", "center_w": 150.0, "runs_window": 6, "median_duration_s": 3000, "name": "Freezer"}]


def test_device_ignores_background_chatter(tmp_path):
    h = Hunt(Circuits(tmp_path / "settings.db"), ChattyDetector(tmp_path / "settings.db"))
    s = Sim(h)
    s.tick(8)
    h.start("device")
    s.settle()
    v = h.view()
    assert v["ignoring"] == [{"legs": "L2", "w": 36.0, "runs_48h": 300, "name": None}]   # the freezer cycles too rarely
    assert "ignoring 1 small background load" in v["prompt"]
    s.tick(deltas=[s.step(2, 38)])          # the relay, again
    s.tick(6)
    assert h.phase == "ready" and h.view()["ignored_steps"] == 1
    s.tick(deltas=[s.step(2, 38)])          # same size on the other leg's twin? no: L1 38 W is not chatter
    s.tick(6)
    s.tick(deltas=[s.step(1, 38)])
    s.tick(6)
    assert h.phase == "on" and h.view()["on"]["legs"] == "L1"
    # retry keeps the filter; include_background turns it off
    h.retry()
    assert h.view()["ignoring"]
    h.start("device", include_background=True)
    s.settle()
    s.tick(deltas=[s.step(2, 38)])
    s.tick(6)
    assert h.phase == "on" and not h.view()["ignoring"]


def test_quick_tap_cycle_keeps_its_off(tmp_path):
    """On and off inside one capture window (a half-second tap) is one full cycle, not a stuck 'on'."""
    h, s = make(tmp_path)
    h.start("device")
    s.settle()
    s.tick(deltas=[s.step(1, 200)])
    s.tick(deltas=[s.step(1, -200)])
    s.tick(6)
    assert h.phase == "measured" and h.view()["measurement"]["cycles"] == 1


def test_stage_count_matches_the_detector(tmp_path):
    """+300 then +1700 one update later: the detector calls that two stages, so must the hunt."""
    from senseless.detect import to_steps
    h, s = make(tmp_path)
    h.start("device")
    s.settle()
    s.tick(deltas=[s.step(1, 300)])
    s.tick(deltas=[s.step(1, 1700)])
    s.tick(6)
    det = to_steps([{"start_frame": 0, "channel": 0, "w": 300, "ts": 0}, {"start_frame": 30, "channel": 0, "w": 1700, "ts": 0.5}])
    assert len(det) == 1 and len(det[0].stages) == 2
    assert h.view()["on"]["stages"] == 2
    out = h.label("Microwave")
    assert out["pattern"]["stages"] == 2


def test_breaker_leg_shift_notifies(tmp_path):
    """Same total, different legs (now a 240 V breaker): clients must hear about it."""
    events = []
    h = Hunt(Circuits(tmp_path / "settings.db"), FakeDetector(tmp_path / "settings.db"), publish=events.append)
    s = Sim(h)
    s.tick(8)
    h.start("breaker")
    s.settle()
    # raw steps at constant leg levels: the drop is exactly the steps, so the total never moves
    s.tick(deltas=[{"channel": 0, "w": -800}])
    s.tick(6)
    n = len(events)
    assert h.view()["drop"]["both"] is False and h.view()["drop"]["total"] == -800
    s.tick(deltas=[{"channel": 0, "w": 400}, {"channel": 1, "w": -400}])
    s.tick(6)
    assert h.view()["drop"]["total"] == -800
    assert h.view()["drop"]["both"] is True and len(events) > n and events[-1]["hunt"]["drop"]["both"] is True


def test_stream_snapshot_carries_hunt(tmp_path):
    async def go():
        st = LiveState()
        h = Hunt()
        app = web.make_app(st, tmp_path / "t.db", hunt=h)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/api/stream")
            line = (await r.content.readline()).decode()
            while not line.startswith("data:"):
                line = (await r.content.readline()).decode()
            import json
            snap = json.loads(line[5:])
            assert snap["hunt"]["phase"] == "idle"
            st.close_streams()
    asyncio.run(go())
