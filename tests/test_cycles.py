"""Synthetic program activity only; captured household data stays outside the repo."""

import asyncio
import json
import math
import sqlite3
from dataclasses import replace
from unittest.mock import Mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request
from aiohttp.web_exceptions import HTTPException

from senseless import cycles, web
from senseless.detect import Detector, Patterns, Run, bin_readings, detect_runs, load_readings, resolve_open_runs
from senseless.state import LiveState
from senseless.store import Store


def delta(t, w, *, channel=0, generation=0):
    return dict(generation=generation, start_frame=t * 60, ts=t, channel=channel, w=w)


def program(start=600, *, heat=False, generation=0, legs="L1"):
    ds = []
    for i in range(121):
        w = 150 + i % 6 * 150
        if heat and i == 50:
            w = 1200
        channels = (0, 1) if legs == "both" else (0,) if legs == "L1" else (1,)
        ds.extend(delta(start + i * 20, w / len(channels), channel=ch, generation=generation) for ch in channels)
    rs = []
    for t in range(start - 180, start + 2461):
        active = start <= t <= start + 2400
        extra = 600 if active else 0
        # Known, nonconstant power: energy must use bin MEANS, not maxima.
        extra += 100 if active and t % 5 == 0 else 0
        rs.append(dict(generation=generation, frame=t * 60, ts=t,
                       l1_w=100 + (extra if legs in ("L1", "both") else 0),
                       l2_w=200 + (extra if legs in ("L2", "both") else 0)))
    return ds, rs


def run_pipeline(tmp_path, ds, rs, now=None):
    now = now if now is not None else max(r["ts"] for r in rs)
    runs = detect_runs(ds, now, rs, keep_expired=True)
    patterns = Patterns(tmp_path / "settings.db")
    patterns.assign(runs)
    resolve_open_runs(runs, now)
    found = cycles.detect_cycles(ds, runs, patterns.all(), rs, now)
    return found, runs, patterns


@pytest.mark.parametrize("legs", ["L1", "L2", "both"])
def test_program_envelope_fingerprint_energy_and_membership(tmp_path, legs):
    ds, rs = program(legs=legs)
    found, runs, _ = run_pipeline(tmp_path, ds, rs)
    c, = found
    factor = 2 if legs == "both" else 1
    assert (c.start_ts, c.end_ts, c.legs, c.steps) == (600, 3000, legs, 121)
    assert c.step_rate == pytest.approx(121 / 40)
    assert (c.median_step_w, c.p90_step_w) == (450, 900)
    assert c.energy_wh == pytest.approx(factor * 620 * 2400 / 3600)
    assert c.peak_w == 700 * factor and not c.has_heat
    assert len(c.member_run_keys) == 121 and all(r.cycle == c.id for r in runs)
    assert runs[0].as_dict(3100)["cycle"] == c.id


def test_dishwasher_heater_is_part_of_program(tmp_path):
    ds, rs = program(heat=True)
    found, _, _ = run_pipeline(tmp_path, ds, rs)
    assert len(found) == 1 and found[0].has_heat


@pytest.mark.parametrize("case", ["flat", "wrong_leg", "no_baseline", "sparse", "null", "nan", "other_generation", "no_readings"])
def test_power_must_confirm_with_covered_baseline_and_correct_leg(tmp_path, case):
    ds, rs = program()
    if case == "flat":
        rs = [dict(r, l1_w=100) for r in rs]
    elif case == "wrong_leg":
        rs = [dict(r, l1_w=100, l2_w=r["l1_w"]) for r in rs]
    elif case == "no_baseline":
        rs = [r for r in rs if r["ts"] >= 600]
    elif case == "sparse":
        rs = rs[::5]
    elif case in ("null", "nan"):
        rs = [dict(r, l1_w=None if case == "null" else math.nan) for r in rs]
    elif case == "other_generation":
        rs = [dict(r, generation=1) for r in rs]
    elif case == "no_readings":
        rs = []
    assert run_pipeline(tmp_path, ds, rs, now=3100)[0] == []


def test_chatter_filter_is_necessary_even_with_unpaired_steps(tmp_path, monkeypatch):
    # A frequent short relay with missed offs can otherwise look like a program.
    # More than half the ons are unpaired, but its known closures have a 10 s median.
    ds = [delta(600 + i * 20, 240) for i in range(86)]
    ds += [delta(610 + i * 20, -240) for i in range(42)]
    rs = [dict(generation=0, frame=t * 60, ts=t, l1_w=100 if t < 600 else 400, l2_w=0)
          for t in range(420, 2401)]
    found, runs, patterns = run_pipeline(tmp_path, ds, rs)
    assert found == []
    assert cycles.chatter_patterns(runs, patterns.all(), 2400)
    monkeypatch.setattr(cycles, "chatter_patterns", lambda *args: set())
    assert len(cycles.detect_cycles(ds, runs, patterns.all(), rs, 2400)) == 1


def test_clean_switching_appliance_is_not_a_cycle(tmp_path):
    ds = [delta(600 + i * 100 + offset, w) for i in range(25) for offset, w in ((0, 5600), (40, -5600))]
    rs = [dict(generation=0, frame=t * 60, ts=t,
               l1_w=100 + (5600 if t >= 600 and (t - 600) % 100 < 40 else 0), l2_w=200)
          for t in range(420, 3101)]
    found, runs, _ = run_pipeline(tmp_path, ds, rs)
    assert all(r.status == "closed" for r in runs) and len(runs) == 25
    assert found == [] and all(r.cycle is None for r in runs)


def test_clean_closures_cannot_bridge_separate_programs(tmp_path):
    ds, _ = program()
    ds = [d for d in ds if 600 <= d["ts"] <= 1200 or 1800 <= d["ts"] <= 2400]
    ds += [delta(605 + i * 100 + offset, w)
           for i in range(18) for offset, w in ((0, 1500), (40, -1500))]
    rs = [dict(generation=0, frame=t * 60, ts=t, l2_w=0,
               l1_w=100 + 600 * (600 <= t <= 1200 or 1800 <= t <= 2400)
               + 1500 * (605 <= t < 2345 and (t - 605) % 100 < 40))
          for t in range(420, 2501)]
    found, runs, _ = run_pipeline(tmp_path, ds, rs)
    clean = [r for r in runs if r.w == 1500]
    assert len(clean) == 18 and all(r.status == "closed" for r in clean)
    assert [(c.start_ts, c.end_ts) for c in found] == [(600, 1200), (1800, 2400)]


@pytest.mark.parametrize("kind,w", [("clean", 1500), ("named", 1100), ("small", 70), ("chatter", 130)])
def test_membership_preserves_independent_and_excluded_loads(tmp_path, kind, w):
    ds, rs = program()
    # All occur inside the program: independent clean appliance, named missed
    # offs, sub-threshold steps, and a frequent background relay with missed offs.
    if kind == "chatter":
        ds += [delta(614 + i * 10, w) for i in range(120)]
        ds += [delta(619 + i * 10, -w) for i in range(50)]
    else:
        ds += [delta(605 + i * 100, w) for i in range(24)]
    if kind == "clean":
        ds += [delta(645 + i * 100, -w) for i in range(24)]
        rs = [dict(r, l1_w=r["l1_w"] + w *
                   (605 <= r["ts"] < 2945 and (r["ts"] - 605) % 100 < 40)) for r in rs]
    detector = make_detector(tmp_path, ds, rs, 3100)
    if kind == "named":
        named = next(p for p in detector.patterns.all() if p["center_w"] == w)
        detector.patterns.name(named["id"], "Known appliance")
        detector.recompute(3100)
    c, = detector.cycles
    independent = [r for r in detector.runs if r.w == w]
    assert len(independent) == (120 if kind == "chatter" else 24)
    assert all(r.cycle is None for r in independent)
    assert all(not p["part_of_cycles"] and p["cycle_runs"] == 0
               for p in detector.loads(0) if p["center_w"] == w)
    assert set(c.member_run_keys) == {cycles.run_key(r) for r in detector.runs if r.cycle == c.id}


@pytest.mark.parametrize("saved", ["name", "ignore"])
def test_growing_cycle_keeps_saved_assignment_across_restart(tmp_path, saved):
    ds, rs = program()
    detector = make_detector(tmp_path, [d for d in ds if d["ts"] <= 900],
                             [r for r in rs if r["ts"] <= 900], 900)
    early, = detector.cycles
    pid = early.pattern
    if saved == "name":
        detector.cycle_patterns.name(pid, "Washer")
    else:
        detector.cycle_patterns.ignore(pid)
    with sqlite3.connect(detector.deltas_db) as conn:
        conn.executemany("INSERT INTO deltas (generation,start_frame,frame,ts,channel,w) "
                         "VALUES (:generation,:start_frame,:start_frame,:ts,:channel,:w)",
                         [d for d in ds if d["ts"] > 900])
        conn.executemany("INSERT INTO readings (generation,frame,ts,l1_w,l2_w) "
                         "VALUES (:generation,:frame,:ts,:l1_w,:l2_w)",
                         [r for r in rs if r["ts"] > 900])
    for now in (3060, 3300):  # growing, then completed after the gap timeout
        detector = Detector(detector.deltas_db, detector.cycle_patterns.db_path)
        detector.recompute(now)
        grown, = detector.cycles
        assert grown.id == early.id and grown.duration_s == 2400
        assert grown.pattern == pid
        p, = detector.cycle_loads(0)
        assert p["name"] == ("Washer" if saved == "name" else None)
        assert p["ignored"] == (saved == "ignore")
        assert p["duration_s"] == 2400 and p["energy_wh"] == pytest.approx(grown.energy_wh)


def test_expired_cycle_evidence_does_not_change_standalone_runs(tmp_path, monkeypatch):
    from senseless.detect import MAX_RUN_S, resolve_closed_runs

    ds, rs = program()
    ds += [delta(100, 1750)]  # missed off, outside the cycle
    ds += [delta(30000, 1500), delta(30040, -1500), delta(30100, 1750)]
    now = 30200
    monkeypatch.setattr("senseless.detect.time.time", lambda: now)
    detector = make_detector(tmp_path, ds, rs, now)
    expected = detect_runs(ds, now, rs)
    legacy = Detector(detector.deltas_db, tmp_path / "legacy-settings.db")
    patterns = legacy.patterns
    patterns.assign(expected)
    resolve_closed_runs(expected, now, set())
    resolve_open_runs(expected, now)
    assert len(detector.runs) == len(expected) == 2
    assert all(r.end_frame is not None or now - r.start_ts <= MAX_RUN_S for r in detector.runs)
    assert [r.as_dict(now) for r in detector.runs] == [r.as_dict(now) for r in expected]
    assert detector.runs_between(0, 30000) == []
    assert [(p["center_w"], p["n"]) for p in detector.patterns.all()] == [
        (p["center_w"], p["n"]) for p in patterns.all()]
    assert sum(p["runs_window"] for p in detector.loads(0)) == 2
    assert all(not p["part_of_cycles"] and p["cycle_runs"] == 0 for p in detector.loads(0))
    legacy.runs = expected
    assert detector.loads(0) == legacy.loads(0)
    # The old program is still detected using evidence expired from run APIs.
    c, = detector.cycles
    assert (c.start_ts, c.end_ts) == (600, 3000)


def test_named_run_patterns_are_excluded(tmp_path):
    ds, rs = program()
    _, runs, patterns = run_pipeline(tmp_path, ds, rs)
    for p in patterns.all():
        patterns.name(p["id"], "Known load")
    assert cycles.detect_cycles(ds, runs, patterns.all(), rs, 3100) == []
    assert all(r.cycle is None for r in runs)  # recompute also clears stale membership


def test_pause_tolerance_and_generation_boundaries(tmp_path):
    ds, rs = program()
    # 220 s between candidate ons stays one cycle.
    ds = [d for d in ds if not 1600 <= d["ts"] < 1800]
    assert len(run_pipeline(tmp_path, ds, rs)[0]) == 1
    # Above the gap threshold splits activity. The second segment's already-high
    # baseline cannot confirm another cycle without a new power rise.
    ds = [d for d in ds if not 1800 <= d["ts"] < 1580 + cycles.GAP_S + 40]
    found, _, _ = run_pipeline(tmp_path, ds, rs)
    assert len(found) == 1 and found[0].end_ts == 1580
    ds2, rs2 = program(start=3600, generation=1)
    found, runs, _ = run_pipeline(tmp_path, ds + ds2, rs + rs2)
    assert len(found) == 2
    assert all(r.generation == c.generation for c in found for r in runs if r.cycle == c.id)


def make_detector(tmp_path, ds, rs, now):
    db = tmp_path / "data.db"
    Store(db).close()
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO deltas (generation,start_frame,frame,ts,channel,w) "
                         "VALUES (:generation,:start_frame,:start_frame,:ts,:channel,:w)", ds)
        conn.executemany("INSERT INTO readings (generation,frame,ts,l1_w,l2_w) "
                         "VALUES (:generation,:frame,:ts,:l1_w,:l2_w)", rs)
    detector = Detector(db, tmp_path / "settings.db")
    detector.recompute(now)
    return detector


def test_two_days_cluster_persist_freeze_and_flag_inbox(tmp_path):
    ds, rs = program()
    ds2, rs2 = program(start=87000)
    detector = make_detector(tmp_path, ds + ds2, rs + rs2, 90000)
    first, second = detector.cycles
    assert first.pattern == second.pattern
    assert all(p["part_of_cycles"] for p in detector.loads(86400))
    p, = detector.cycle_loads(86400)
    assert (p["n"], p["cycles_today"], p["cycles_window"]) == (2, 1, 2)
    assert p["kwh_window"] == pytest.approx(2 * p["kwh_today"])
    assert p["last_seen"] == second.start_ts and p["median_duration_s"] == 2400
    cp = detector.cycle_patterns
    cp.ignore(p["id"])
    assert cp.name(p["id"], " Washer ")["ignored"] == 0
    changed = replace(second, energy_wh=second.energy_wh * 1.2, step_rate=3.5)
    cp.assign([changed], 90000)
    assert cp.all()[0]["energy_wh"] == p["energy_wh"]
    assert cp.all()[0]["step_rate"] == p["step_rate"]
    restarted = Detector(detector.deltas_db, cp.db_path)
    restarted.recompute(90000)
    assert len(restarted.cycles) == 2
    assert {c["name"] for c in restarted.cycles_between(0, 91000)} == {"Washer"}
    assert [c.id for c in restarted.cycles] == [first.id, second.id]
    assert restarted.cycles_between(first.end_ts, second.start_ts) == []
    assert len(restarted.cycles_between(first.start_ts + 1, first.end_ts - 1)) == 1
    # Midnight energy is apportioned; cycle count uses start time.
    stats = restarted.cycle_loads(second.start_ts + 1200)[0]
    assert stats["cycles_today"] == 0 and stats["kwh_today"] == pytest.approx(p["kwh_today"] / 2)
    restarted.recompute(90000 + 3 * 86400)
    assert restarted.cycle_loads(90000)[0]["cycles_window"] == 0


def test_cycle_pattern_matching_limits_nearest_and_pruning(tmp_path):
    ds, rs = program()
    c, = run_pipeline(tmp_path, ds, rs)[0]
    cp = cycles.CyclePatterns(tmp_path / "settings.db")
    other_leg = replace(c, legs="L2")
    far = replace(c, start_frame=c.start_frame + 600000, energy_wh=c.energy_wh * 1.5)
    cp.assign([c, other_leg, far], 4000)
    assert len({c.pattern, other_leg.pattern, far.pattern}) == 3
    nearby = replace(c, start_frame=c.start_frame + 1200000, energy_wh=c.energy_wh * 1.4)
    cp.assign([nearby], 4100)
    assert nearby.pattern == far.pattern  # within both energy gates; nearest wins
    cp.name(c.pattern, "Washer")
    cp.ignore(other_leg.pattern)
    cp.assign([], 3200 + 15 * 86400)
    assert {p["id"] for p in cp.all()} == {c.pattern, other_leg.pattern}
    assert cp.name(c.pattern, " ")["name"] is None
    with pytest.raises(KeyError):
        cp.name(99999, "Missing")
    with pytest.raises(KeyError):
        cp.ignore(99999)


def test_growing_cycles_cluster_only_after_completion(tmp_path):
    cp = cycles.CyclePatterns(tmp_path / "settings.db")
    short = cycles.Cycle(0, 36000, 600, 900, "L1", 16, 3.2, 450, 900, 50, 700, False)
    cp.assign([short], 1200)
    cp.name(short.pattern, "Short program")
    growing = replace(short, start_frame=216000, start_ts=3600, end_ts=3900)
    cp.assign([short, growing], 3900)
    assert growing.pattern != short.pattern
    provisional_id = growing.pattern
    grown = replace(growing, end_ts=6000, energy_wh=400)
    cp.assign([short, grown], 6100)
    assert grown.pattern == provisional_id
    assert next(p for p in cp.all() if p["id"] == short.pattern)["duration_s"] == 300
    cp.name(grown.pattern, "Washer")
    cp.assign([short, grown], 6000 + cycles.GAP_S + 60)     # complete: idle longer than the gap
    p = next(p for p in cp.all() if p["id"] == grown.pattern)
    assert p["duration_s"] == 2400 and p["energy_wh"] == 400 and not p["provisional"]
    # Another wash seen while growing stays independent, then joins the final
    # named centre when complete. Its prefix must not alter the named centre.
    next_wash = replace(growing, start_frame=432000, start_ts=7200, end_ts=7500)
    cp.assign([grown, next_wash], 7500)
    assert next_wash.pattern != grown.pattern
    final_wash = replace(next_wash, end_ts=9600, energy_wh=400)
    cp.assign([grown, final_wash], 9600 + cycles.GAP_S + 60)
    assert final_wash.pattern == grown.pattern
    assert next(p for p in cp.all() if p["id"] == grown.pattern)["n"] == 2


def test_sql_cycle_means_match_raw_bins(tmp_path):
    ds, rs = program()
    detector = make_detector(tmp_path, ds, rs, 3100)
    a = load_readings(detector.deltas_db, 0)
    b = bin_readings(rs)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        for leg in ("L1", "L2", "both"):
            assert x[leg + "_mean"] == pytest.approx(y[leg + "_mean"])
    found, _, _ = run_pipeline(tmp_path, ds, rs)
    assert detector.cycles[0].energy_wh == pytest.approx(found[0].energy_wh)


def test_fragment_flag_requires_seventy_percent_of_window_runs(tmp_path):
    Store(tmp_path / "data.db").close()
    detector = Detector(tmp_path / "data.db", tmp_path / "settings.db")
    detector.runs = [Run(i * 1200, i * 20, "L1", 450, [(0, 450)], status="unresolved",
                         cycle="wash" if i < 7 else None) for i in range(10)]
    detector.patterns.assign(detector.runs)
    p, = detector.loads(0)
    assert p["part_of_cycles"] and p["cycle_runs"] == 7
    detector.runs[-1].cycle = "wash"  # 8/11 still passes after a later unassociated run
    detector.runs.append(Run(12000, 200, "L1", 450, [(0, 450)], status="unresolved"))
    detector.patterns.assign(detector.runs)
    assert detector.loads(0)[0]["part_of_cycles"]
    detector.runs[-2].cycle = None  # now 7/11; keep the pattern visible in the inbox
    assert not detector.loads(0)[0]["part_of_cycles"]


@pytest.mark.parametrize("feature,scale", [("duration_s", 1.36), ("energy_wh", 1.41), ("median_step_w", 1.41)])
def test_cycle_pattern_feature_gates(tmp_path, feature, scale):
    c = cycles.Cycle(0, 36000, 600, 3000, "L1", 121, 3.025, 450, 900, 400, 700, False)
    cp = cycles.CyclePatterns(tmp_path / "settings.db")
    cp.assign([c], 4000)
    changes = {feature: getattr(c, feature) * scale} if feature != "duration_s" else {"end_ts": c.start_ts + c.duration_s * scale}
    other = replace(c, start_frame=c.start_frame + 600000, **changes)
    cp.assign([c, other], 5000)
    assert other.pattern != c.pattern


def test_cycle_routes_and_metrics_without_sockets(tmp_path, monkeypatch):
    ds, rs = program()
    detector = make_detector(tmp_path, ds, rs, 3100)
    monkeypatch.setattr(web.time, "time", lambda: 3100)
    monkeypatch.setattr(web.summary, "day_bounds", lambda: (0, 86400))

    async def run():
        app = web.make_app(LiveState(), detector.deltas_db, detector=detector)

        async def request(method, path, body=None, headers=None):
            payload = StreamReader(Mock(_reading_paused=False), 65536, loop=asyncio.get_running_loop())
            payload.feed_data(json.dumps(body).encode() if body is not None else b"")
            payload.feed_eof()
            req = make_mocked_request(method, path, app=app, payload=payload,
                                      headers={"Host": "localhost", "Content-Type": "application/json", **(headers or {})})
            req._match_info = await app.router.resolve(req)
            # Execute the real middleware too, so same-origin and JSON guards are tested.
            handler = req._match_info.handler
            try:
                for middleware in reversed(app.middlewares):
                    inner = handler
                    async def handler(request, m=middleware, h=inner):
                        return await m(request, h)
                return await handler(req)
            except HTTPException as exc:
                return exc

        response = await request("GET", "/api/cycles?minutes=60")
        assert response.status == 200
        c, = json.loads(response.text)
        pid = c["pattern"]
        base = f"/api/cycle-patterns/{pid}"
        assert (await request("POST", base + "/name", {"name": 3})).status == 400
        assert (await request("POST", base + "/name", {"name": "Washer"}, {"Origin": "http://evil.example"})).status == 403
        assert (await request("POST", base + "/name", {"name": "Washer"})).status == 200
        assert json.loads((await request("GET", "/api/cycles?start=700&end=800")).text)[0]["name"] == "Washer"
        assert (await request("POST", base + "/ignore", {"ignored": "yes"})).status == 400
        assert json.loads((await request("POST", base + "/ignore", {"ignored": True})).text)["ignored"] == 1
        p = json.loads((await request("GET", "/api/cycle-patterns")).text)["patterns"][0]
        assert p["cycles_today"] == 1 and p["name"] == "Washer"
        assert all(p["part_of_cycles"] for p in json.loads((await request("GET", "/api/loads")).text)["patterns"])
        assert all(r["cycle"] == c["id"] for r in json.loads((await request("GET", "/api/runs?minutes=60")).text))
        assert (await request("GET", "/api/cycles?minutes=no")).status == 400
        assert (await request("POST", "/api/cycle-patterns/x/name", {})).status == 400
        assert (await request("POST", "/api/cycle-patterns/999/name", {})).status == 404
        assert (await request("POST", "/api/cycle-patterns/999/ignore", {})).status == 404
        assert 'senseless_cycles_today{name="Washer"} 1' in (await request("GET", "/metrics")).text
        assert (await request("POST", base + "/name", {"name": None})).status == 200
        assert json.loads((await request("GET", "/api/cycles?minutes=60")).text)[0]["name"] is None

    asyncio.run(run())
