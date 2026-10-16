import json
from pathlib import Path

import pytest

from senseless.detect import Patterns, pair_runs, to_steps

SAMPLE = Path(__file__).parent.parent / "samples" / "microwave-30s.jsonl"


def sample_deltas():
    out = []
    for line in SAMPLE.open():
        r = json.loads(line)
        if r["msg"]["type"] == "realtime_update":
            for d in r["msg"]["payload"].get("deltas") or []:
                out.append({"start_frame": d["start_frame"], "channel": d["channel"], "w": d["w"], "ts": r["t"]})
    return out


def d(frame, ch, w, ts=None):
    return {"start_frame": frame, "channel": ch, "w": w, "ts": ts if ts is not None else frame / 60}


def test_microwave_is_one_two_stage_run():
    runs = pair_runs(to_steps(sample_deltas()), now_ts=1e12)
    big = [r for r in runs if r.w > 1000]
    assert len(big) == 1
    mw = big[0]
    assert mw.legs == "L2" and len(mw.stages) == 2
    assert mw.stages[1][0] == 90                      # second stage 90 cycles after the first
    assert mw.w == pytest.approx(299 + 1726, abs=2)
    assert not mw.running and mw.off_w == pytest.approx(-1907, abs=1)
    assert mw.duration_s(0) == pytest.approx(29.5, abs=0.1)


def test_240v_steps_pair_into_one_both_legs_run():
    steps = to_steps([d(100, 0, 1217), d(100, 1, 1232), d(100 + 60 * 180, 0, -1210), d(100 + 60 * 180, 1, -1225)])
    assert [s.legs for s in steps] == ["both", "both"]
    runs = pair_runs(steps, now_ts=1e12)
    assert len(runs) == 1 and runs[0].legs == "both" and runs[0].duration_s(0) == pytest.approx(180)


def test_other_leg_does_not_break_stages_but_opposite_sign_does():
    s = to_steps([d(0, 1, 300), d(30, 0, 50), d(90, 1, 1700)])
    assert [(x.legs, len(x.stages)) for x in s] == [("L2", 2), ("L1", 0)]
    s = to_steps([d(0, 1, 300), d(30, 1, -300), d(90, 1, 1700)])
    assert len(s) == 3


def test_cycler_pairs_each_on_with_its_off_and_noise_is_dropped():
    deltas = []
    f = 0
    for _ in range(10):
        deltas += [d(f, 1, 45), d(f + 300, 1, -46)]   # 45 W on for 5 s, then off
        f += 600
    deltas.append(d(f, 1, 12))                        # noise floor: ignored
    runs = pair_runs(to_steps(deltas), now_ts=1e12)
    assert len(runs) == 10 and all(not r.running and r.duration_s(0) == pytest.approx(5) for r in runs)


def test_open_runs_resolve_against_their_patterns_typical_duration():
    from senseless.detect import resolve_open_runs
    deltas = [d(0, 1, 45, 0), d(300, 1, -45, 5), d(600, 1, 45, 10), d(900, 1, -45, 15),  # 5 s runs
              d(1200, 1, 45, 20)]                                                       # missed off
    runs = pair_runs(to_steps(deltas), now_ts=20 + 3600)
    for r in runs:
        r.pattern = 1
    resolve_open_runs(runs, now_ts=20 + 3600)
    assert [r.status for r in runs] == ["closed", "closed", "unresolved"]
    assert runs[-1].duration_s(20 + 3600) == 0            # no phantom energy
    runs = pair_runs(to_steps(deltas), now_ts=20 + 5)
    for r in runs:
        r.pattern = 1
    resolve_open_runs(runs, now_ts=20 + 5)
    assert runs[-1].status == "running"


def test_unclosed_run_is_running_until_too_old():
    runs = pair_runs(to_steps([d(0, 0, 500, ts=1000.0)]), now_ts=1000.0 + 60)
    assert runs[0].running
    assert pair_runs(to_steps([d(0, 0, 500, ts=1000.0)]), now_ts=1000.0 + 7 * 3600) == []


def test_off_closes_most_recent_compatible_on():
    runs = pair_runs(to_steps([d(0, 1, 500), d(600, 1, 100), d(1200, 1, -505)]), now_ts=60)
    big = next(r for r in runs if r.w == 500)
    small = next(r for r in runs if r.w == 100)
    assert big.end_frame == 1200 and small.running


def test_patterns_group_similar_runs_and_names_persist(tmp_path):
    p = Patterns(tmp_path / "s.db")
    deltas = []
    f = 0
    for w in (44, 46, 45, 47, 495, 505):
        deltas += [d(f, 1, w), d(f + 300, 1, -w)]
        f += 1000
    runs = pair_runs(to_steps(deltas), now_ts=1e12)
    p.assign(runs)
    ids = {r.w: r.pattern for r in runs}
    assert ids[44] == ids[46] == ids[45] == ids[47] != ids[495] == ids[505]
    p.name(ids[45], "Aquarium heater")
    # a fresh recompute (new Patterns object, same db) maps onto the named pattern
    runs2 = pair_runs(to_steps(deltas), now_ts=1e12)
    Patterns(tmp_path / "s.db").assign(runs2)
    assert {r.pattern for r in runs2 if r.w < 100} == {ids[45]}
    named = next(x for x in p.all() if x["id"] == ids[45])
    assert named["name"] == "Aquarium heater" and named["n"] == 4
    with pytest.raises(KeyError):
        p.name(9999, "nope")


def _store_with_runs(tmp_path):
    import time as _t
    from senseless.store import Store
    s = Store(tmp_path / "senseless.db")
    now = _t.time()
    f = 1_000_000
    for i in range(6):                         # 6 x (45 W on 5 s), recent
        ts = now - 600 + i * 30
        s.realtime({"frame": f, "epoch": ts, "w": 1000, "deltas": [{"start_frame": f, "frame": f, "channel": 1, "w": 45}]}, ts)
        s.realtime({"frame": f + 300, "epoch": ts + 5, "w": 1000, "deltas": [{"start_frame": f + 300, "frame": f + 300, "channel": 1, "w": -45}]}, ts + 5)
        f += 1800
    s.close()
    return tmp_path / "senseless.db"


def test_loads_api_name_and_ignore(tmp_path):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    from senseless import web
    from senseless.detect import Detector
    from senseless.state import LiveState

    db = _store_with_runs(tmp_path)
    det = Detector(db, tmp_path / "settings.db")
    det.recompute()

    async def run():
        async with TestClient(TestServer(web.make_app(LiveState(), db, detector=det))) as c:
            loads = (await (await c.get("/api/loads")).json())["patterns"]
            assert len(loads) == 1 and loads[0]["runs_window"] == 6 and loads[0]["closed_runs"] == 6
            assert loads[0]["median_duration_s"] == 5.0 and loads[0]["legs"] == "L2"
            pid = loads[0]["id"]
            r = await c.post(f"/api/loads/{pid}/name", json={"name": "  Aquarium heater  "})
            assert r.status == 200 and (await r.json())["name"] == "Aquarium heater"
            assert (await c.post("/api/loads/9999/name", json={"name": "x"})).status == 404
            assert (await c.post("/api/loads/abc/name", json={"name": "x"})).status == 400
            assert (await c.post(f"/api/loads/{pid}/name", json={"name": 5})).status == 400
            assert (await c.post(f"/api/loads/{pid}/ignore", json={"ignored": True}, headers={"Origin": "http://evil.example"})).status == 403
            r = await c.post(f"/api/loads/{pid}/ignore", json={"ignored": True})
            assert (await r.json())["ignored"] == 1
            runs = await (await c.get("/api/runs?minutes=60")).json()
            assert len(runs) == 6 and all(x["pattern"] == pid for x in runs)
    asyncio.run(run())


def test_unnamed_unseen_patterns_are_pruned_but_named_kept(tmp_path):
    import sqlite3, time as _t
    from senseless.detect import Patterns
    p = Patterns(tmp_path / "s.db")
    old = _t.time() - 30 * 86400
    with sqlite3.connect(tmp_path / "s.db") as db:
        db.execute("INSERT INTO patterns (legs, stages, center_w, n, created, last_seen) VALUES ('L1',1,77,0,?,?)", (old, old))
        db.execute("INSERT INTO patterns (legs, stages, center_w, n, created, last_seen, name) VALUES ('L1',1,500,0,?,?,'Kettle')", (old, old))
    p.assign([])
    assert [x["name"] for x in p.all()] == ["Kettle"]


def test_short_pattern_is_not_running_minutes_later():
    from senseless.detect import resolve_open_runs
    deltas = [d(0, 0, 55, 0), d(780, 0, -55, 13), d(1200, 0, 55, 20), d(1980, 0, -55, 33), d(2400, 0, 55, 40)]
    runs = pair_runs(to_steps(deltas), now_ts=40 + 120)
    for r in runs:
        r.pattern = 1
    resolve_open_runs(runs, now_ts=40 + 120)
    assert runs[-1].status == "unresolved"   # 13 s median: 2 minutes "on" is a missed off


# ── regressions from the M2 review ──────────────────────────────────────────

def test_stale_on_is_not_closed_by_a_drop_a_day_later():
    runs = pair_runs(to_steps([d(0, 0, 500, ts=0), d(60 * 86400, 0, -500, ts=86400)]), now_ts=86400 + 1)
    assert all(r.end_frame is None for r in runs)       # no 24 h "closed" run


def test_two_offs_a_second_apart_close_their_own_runs():
    runs = pair_runs(to_steps([d(0, 1, 500), d(600, 1, 100), d(1200, 1, -500), d(1260, 1, -100)]), now_ts=60)
    assert sorted((r.w, r.end_frame) for r in runs) == [(100, 1260), (500, 1200)]


def test_runs_before_a_counter_reset_survive():
    from senseless.detect import detect_runs
    deltas = [{"generation": 0, "start_frame": 5_000_000, "channel": 0, "w": 300, "ts": 100},
              {"generation": 0, "start_frame": 5_000_600, "channel": 0, "w": -300, "ts": 110},
              {"generation": 1, "start_frame": 1_000, "channel": 1, "w": 700, "ts": 200},
              {"generation": 1, "start_frame": 1_600, "channel": 1, "w": -700, "ts": 210}]
    runs = detect_runs(deltas, now_ts=300)
    assert sorted((r.generation, r.w, r.status) for r in runs) == [(0, 300, "closed"), (1, 700, "closed")]


def test_energy_today_counts_the_part_after_midnight(tmp_path):
    from senseless.detect import Detector, Run
    det = Detector(tmp_path / "unused.db", tmp_path / "s.db")
    midnight = 1_800_000_000.0
    r = Run(0, midnight - 1800, "L1", 1000.0, [(0, 1000.0)], end_frame=3600 * 60, end_ts=midnight + 1800, off_w=-1000, status="closed")
    det.patterns.assign([r])
    det.runs = [r]
    load = det.loads(midnight)[0]
    assert load["wh_today"] == 500.0 and load["runs_today"] == 0


def test_named_centre_is_frozen_even_if_named_after_the_snapshot(tmp_path):
    import sqlite3
    p = Patterns(tmp_path / "s.db")
    first = pair_runs(to_steps([d(0, 0, 100), d(600, 0, -100)]), now_ts=1e12)
    p.assign(first)
    pid = first[0].pattern
    real = p._snapshot

    def snapshot_then_named_by_api(db):
        rows = real(db)                      # assign() sees the pattern unnamed...
        with sqlite3.connect(tmp_path / "s.db") as other:
            other.execute("UPDATE patterns SET name='Lamp' WHERE id=?", (pid,))  # ...then the API names it
        return rows

    p._snapshot = snapshot_then_named_by_api
    p.assign(pair_runs(to_steps([d(0, 0, 110), d(600, 0, -110), d(1200, 0, 110), d(1800, 0, -110)]), now_ts=1e12))
    row = next(x for x in Patterns(tmp_path / "s.db").all() if x["id"] == pid)
    assert row["name"] == "Lamp" and row["center_w"] == 100   # named signature not moved to 110
