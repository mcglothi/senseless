"""Synthetic missed-off regressions; no captured account data."""

import math
import sqlite3

import pytest

from senseless.detect import Detector, MAX_RUN_S, detect_runs, resolve_open_runs


def delta(seconds, w, channel=0, generation=0):
    return dict(start_frame=int(seconds * 60), ts=seconds, channel=channel,
                generation=generation, w=w)


def readings(stop, power, *, generation=0):
    """One reading/second; power(t) returns the two leg levels."""
    return [dict(frame=t * 60, ts=t, generation=generation,
                 l1_w=power(t)[0], l2_w=power(t)[1]) for t in range(stop + 1)]


@pytest.mark.parametrize("w,off,low", [(240, -265, 70), (145, -145, 80)])
def test_missed_off_cannot_close_with_unrelated_drop_five_hours_later(w, off, low):
    ds = [delta(0, w), delta(900, -w * .65), delta(18000, off)]
    rs = readings(18010, lambda t: (w + 100 if t < 900 or t >= 17500 else low, 900))
    run, = detect_runs(ds, 18010, rs)
    assert run.status == "unresolved" and run.end_frame is None
    assert run.duration_s(18010) == 0 and run.as_dict(18010)["wh"] == 0
    # Pattern timing must not revive a run that power evidence already retired.
    run.pattern = 1
    resolve_open_runs([run], 100)
    assert run.status == "unresolved"


def test_newer_cycle_disproves_two_similar_loads_remaining_on_together():
    # The old load's off is missed. Other background loads change too: no leg
    # reading is below the old on's individual 80 W floor. During the newer
    # 105 W cycle, though, 125 W cannot sustain BOTH putative loads (80+85 W).
    ds = [delta(0, 100), delta(600, -60), delta(1200, 105),
          delta(1800, -105), delta(18000, -100)]
    rs = readings(18010, lambda t: (125 if 1200 <= t < 1800 else 120, 900))
    old, new = detect_runs(ds, 18010, rs)
    assert old.status == "unresolved" and old.end_frame is None
    assert new.status == "closed" and new.duration_s(0) == 600


@pytest.mark.parametrize("duration", [3600, 7200, MAX_RUN_S])
@pytest.mark.parametrize("legs", ["L1", "L2", "both"])
def test_genuine_long_load_survives_with_interleaved_cycles(duration, legs):
    channels = (0, 1) if legs == "both" else (0,) if legs == "L1" else (1,)
    ds = [delta(t, w, ch) for t, w in ((0, 100), (duration, -100)) for ch in channels]
    # Similar loads can genuinely overlap, including complete shorter cycles
    # nested inside a long run. Power supports both throughout each overlap.
    for start in (300, 900, 1500):
        ds += [delta(t, w, ch) for t, w in ((start, 100), (start + 120, -100)) for ch in channels]
    def power(t):
        active = (t < duration) + sum(start <= t < start + 120 for start in (300, 900, 1500))
        return tuple(30 + 100 * active if ch in channels else 10 for ch in (0, 1))
    runs = detect_runs(ds, duration + 10, readings(duration + 10, power))
    assert [r.duration_s(0) for r in runs] == [duration, 120, 120, 120]
    assert all(r.status == "closed" and r.legs == legs for r in runs)


def test_invalid_newest_candidate_does_not_steal_off_from_supported_older_on():
    ds = [delta(0, 100), delta(10, 125), delta(100, -110)]
    rs = readings(110, lambda t: (90 if 40 <= t < 70 else 250, 900))
    old, new = detect_runs(ds, 110, rs)
    assert old.status == "closed" and old.duration_s(0) == 100
    assert new.status == "unresolved" and new.end_frame is None


@pytest.mark.parametrize("evidence", ["none", "null", "nan", "sparse", "gap", "transient", "other_leg", "other_generation"])
def test_missing_or_unrelated_evidence_and_isolated_dips_preserve_pairing(evidence):
    ds = [delta(0, 240), delta(7200, -240)]
    rs = readings(7210, lambda t: (300, 5))
    if evidence == "none":
        rs = []
    elif evidence in ("null", "nan"):
        for row in rs:
            row["l1_w"] = None if evidence == "null" else math.nan
    elif evidence == "sparse":
        rs = [dict(row, l1_w=0) for row in rs[::5]]
    elif evidence == "gap":
        # Low samples at bin edges do not fill the missing middle.
        rs = [dict(row, l1_w=0) for row in rs if row["ts"] % 5 in (0, 4)]
    elif evidence == "transient":
        rs[3600]["l1_w"] = 0
    elif evidence == "other_generation":
        rs = [dict(row, l1_w=0, generation=1) for row in rs]
    run, = detect_runs(ds, 7210, rs)
    assert run.status == "closed" and run.duration_s(0) == 7200


def test_startup_stages_and_the_actual_off_are_not_contradictions():
    ds = [delta(t, 100) for t in range(0, 13, 2)] + [delta(100, -700)]
    rs = readings(110, lambda t: (100 + 100 * (t // 2) if t < 12 else 750 if t < 100 else 10, 900))
    run, = detect_runs(ds, 110, rs)
    assert run.status == "closed" and len(run.stages) == 7
    assert run.duration_s(0) == 100


def test_both_uses_total_power_without_assuming_exactly_balanced_legs():
    ds = [delta(0, 750), delta(0, 1000, 1), delta(3600, -750), delta(3600, -1000, 1)]
    run, = detect_runs(ds, 3610, readings(3610, lambda t: (750, 1000) if t < 3600 else (0, 0)))
    assert run.legs == "both" and run.duration_s(0) == 3600


def test_readings_are_scoped_to_counter_generation():
    ds = [delta(0, 240), delta(3600, -240),
          delta(0, 240, generation=1), delta(3600, -240, generation=1)]
    rs = readings(3610, lambda t: (300, 900))
    rs += readings(3610, lambda t: (50 if 1200 <= t < 1800 else 300, 900), generation=1)
    old, new = detect_runs(ds, 3610, list(reversed(rs)))
    assert old.status == "closed" and old.generation == 0
    assert new.status == "unresolved" and new.generation == 1


def test_retired_open_runs_still_expire_at_six_hours():
    ds = [delta(0, 240)]
    rs = readings(20, lambda t: (20, 900))
    run, = detect_runs(ds, 30, rs)
    assert run.status == "unresolved"
    assert detect_runs(ds, MAX_RUN_S + 1, rs) == []


def test_detector_recompute_loads_power_evidence(tmp_path):
    from senseless.store import Store
    db = tmp_path / "data.db"
    Store(db).close()
    ds = [delta(0, 240), delta(18000, -240)]
    rs = readings(18010, lambda t: (70 if 1200 <= t < 1700 else 340, 900))
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO deltas (generation,start_frame,frame,ts,channel,w) "
                         "VALUES (:generation,:start_frame,:start_frame,:ts,:channel,:w)", ds)
        conn.executemany("INSERT INTO readings (generation,frame,ts,l1_w,l2_w) "
                         "VALUES (:generation,:frame,:ts,:l1_w,:l2_w)", rs)
    detector = Detector(db, tmp_path / "settings.db")
    detector.recompute(18010)
    run, = detector.runs
    assert run.status == "unresolved" and run.end_frame is None
    assert detector.loads(0)[0]["wh_window"] == 0


def test_sql_binning_matches_python(tmp_path):
    """load_readings bins in SQL; bin_readings is the same rule in Python. They must agree, gaps,
    missing values and generations included."""
    import math as _m
    import random
    import sqlite3 as _sq
    from senseless.detect import bin_readings, load_readings
    from senseless.store import Store
    Store(tmp_path / "s.db")
    rnd = random.Random(7)
    rows = []
    for gen in (0, 1):
        f = 0
        while f < 60 * 60 * 20:
            f += 150 if rnd.random() < 0.01 else rnd.choice([29, 30, 31])   # ~2/s, occasional 2.5 s gaps
            l1 = None if rnd.random() < 0.002 else rnd.uniform(50, 900)
            rows.append({"generation": gen, "frame": f, "ts": 1000 + f / 60, "l1_w": l1, "l2_w": rnd.uniform(50, 900)})
    with _sq.connect(tmp_path / "s.db") as conn:
        conn.executemany("INSERT INTO readings (generation,frame,ts,l1_w,l2_w) VALUES (:generation,:frame,:ts,:l1_w,:l2_w)", rows)
    sql = load_readings(tmp_path / "s.db", 0)
    py = [b for g in (0, 1) for b in bin_readings([r for r in rows if r["generation"] == g])]
    assert len(sql) == len(py) > 100
    for a, b in zip(sql, py):
        assert (a["generation"], a["bin_start"]) == (b["generation"], b["bin_start"])
        for leg in ("L1", "L2", "both"):
            assert (_m.isinf(a[leg]) and _m.isinf(b[leg])) or abs(a[leg] - b[leg]) < 1e-6
