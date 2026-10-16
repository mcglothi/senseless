"""Synthetic settled-level regressions; real replay data stays outside the repo."""

import math
import sqlite3

import pytest

from senseless.detect import (
    Detector, Patterns, StartupPower, detect_runs, load_deltas, load_readings,
    load_startup_readings,
)
from senseless.store import Store


def delta(t, w, channel=0, generation=0):
    return dict(generation=generation, start_frame=int(t * 60), frame=int(t * 60),
                ts=t, channel=channel, w=w)


def motor(*, legs="L1", on=2200, level=1000, start=30, stop=130, generation=0):
    channels = (0, 1) if legs == "both" else (0,) if legs == "L1" else (1,)
    ds = [delta(t, w / len(channels), ch, generation)
          for t, w in ((start, on), (stop, -1.05 * level)) for ch in channels]
    rs = []
    for t in range(int(stop) + 11):
        active = start <= t < stop
        rise = on if start <= t < start + 3 else level
        values = [100 + (rise / len(channels) if active and ch in channels else 0) for ch in (0, 1)]
        rs.append(dict(generation=generation, frame=t * 60, ts=t, l1_w=values[0], l2_w=values[1]))
    return ds, rs


@pytest.mark.parametrize("legs", ["L1", "L2", "both"])
def test_surge_sizes_by_settled_power_and_closes(legs):
    ds, rs = motor(legs=legs)
    run, = detect_runs(ds, 140, rs)
    assert run.w == pytest.approx(1000)
    assert run.inrush_w == 2200 and run.legs == legs
    assert run.status == "closed" and run.end_frame == 130 * 60
    assert run.duration_s(140) == 100
    assert run.as_dict(140)["wh"] == 27.78
    assert run.as_dict(140)["inrush_w"] == 2200
    assert run.stages == [(0, 2200)]  # preserve the monitor's reported stages


@pytest.mark.parametrize("off,closes", [(800, True), (799, False), (1170, True),
                                       (1200, True), (1201, False)])
@pytest.mark.parametrize("surge", [True, False])
def test_only_surge_sized_runs_get_twenty_percent_pairing(off, closes, surge):
    ds, rs = motor(on=2200 if surge else 1000)
    ds[-1]["w"] = -off
    run, = detect_runs(ds, 140, rs)
    expected = closes if surge else abs(1000 - off) <= 150
    assert (run.end_frame == 130 * 60) is expected
    assert (run.inrush_w is not None) is surge


@pytest.mark.parametrize("level,on", [(1000, 1000), (1000, 1250), (29, 2200), (100, 299)])
def test_non_surge_or_subthreshold_on_is_untouched(level, on):
    ds, rs = motor(on=on, level=level)
    run, = detect_runs(ds, 140, rs)
    assert run.w == on and run.as_dict(140)["inrush_w"] is None


@pytest.mark.parametrize("t,w,channel,adjust", [
    (50, 120, 0, False), (50, -120, 0, False),
    (50.5, 500, 0, True), (40, 119, 0, True), (40, 500, 1, True),
])
def test_switching_inside_settle_window_skips_adjustment(t, w, channel, adjust):
    ds, rs = motor()
    ds.append(delta(t, w, channel))
    run = detect_runs(ds, 140, rs)[0]
    assert (run.inrush_w is not None) is adjust
    assert run.w == (1000 if adjust else 2200)


def test_overlap_guard_uses_exact_leg_categories():
    ds, rs = motor(legs="both")
    ds.append(delta(40, -120, 1))
    assert detect_runs(ds, 140, rs)[0].inrush_w == 2200
    ds += [delta(45, 200, ch) for ch in (0, 1)]
    assert detect_runs(ds, 140, rs)[0].inrush_w is None


def test_last_stage_anchors_settled_window_and_first_stage_anchors_baseline():
    ds, rs = motor(start=30, on=2200)
    ds[0]["w"] = 200
    ds += [delta(t, 200) for t in range(32, 51, 2)]
    for row in rs:
        if 30 <= row["ts"] < 56:
            row["l1_w"] = 2300
    run, = detect_runs(ds, 140, rs)
    assert run.w == 1000 and run.inrush_w == 2200
    assert len(run.stages) == 11 and run.stages[-1][0] == 20 * 60
    # A separate event after t+20 but inside last-stage+20 still blocks adjustment.
    ds.append(delta(60, -120))
    assert detect_runs(ds, 140, rs)[0].inrush_w is None


@pytest.mark.parametrize("kind", ["none", "pre", "settled", "null", "nan", "inf", "other_generation", "coarse"])
def test_missing_ambiguous_or_coarse_readings_keep_reported_size(kind):
    ds, rs = motor()
    if kind == "none":
        rs = []
    elif kind in ("pre", "settled"):
        low, high = (18, 28) if kind == "pre" else (36, 50)
        rs = [r for r in rs if not low <= r["ts"] < high]
    elif kind in ("null", "nan", "inf"):
        for r in rs:
            r["l1_w"] = {"null": None, "nan": math.nan, "inf": math.inf}[kind]
    elif kind == "other_generation":
        rs = [dict(r, generation=1) for r in rs]
    elif kind == "coarse":
        from senseless.detect import bin_readings
        rs = bin_readings(rs)
    run, = detect_runs(ds, 140, rs)
    assert run.w == 2200 and run.inrush_w is None


@pytest.mark.parametrize("missing,adjust", [(3, True), (4, False)])
def test_requires_seventy_percent_baseline_coverage(missing, adjust):
    ds, rs = motor()
    rs = [r for r in rs if not 18 <= r["ts"] < 18 + missing]
    run, = detect_runs(ds, 140, rs)
    assert (run.inrush_w is not None) is adjust


def test_generation_scoping_and_unsorted_raw_readings():
    ds, rs = motor()
    ds2, rs2 = motor(generation=1, level=800)
    runs = detect_runs(ds + ds2, 140, list(reversed(rs + rs2)))
    assert [(r.generation, r.w, r.status) for r in runs] == [(0, 1000, "closed"), (1, 800, "closed")]


def store_rows(db, ds, rs):
    Store(db).close()
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO deltas (generation,start_frame,frame,ts,channel,w) "
                         "VALUES (:generation,:start_frame,:frame,:ts,:channel,:w)", ds)
        conn.executemany("INSERT INTO readings (generation,frame,ts,l1_w,l2_w) "
                         "VALUES (:generation,:frame,:ts,:l1_w,:l2_w)", rs)


def test_sql_recompute_clusters_running_size_and_persists_median_inrush(tmp_path):
    ds, rs = motor()
    ds2, rs2 = motor(on=3000, start=230, stop=330)
    # Only retain the second activation's part of the second fixture.
    rs += [r for r in rs2 if r["ts"] > 140]
    db = tmp_path / "data.db"
    store_rows(db, ds + ds2, rs)
    det = Detector(db, tmp_path / "settings.db")
    det.recompute(340)
    assert [r.w for r in det.runs] == [1000, 1000]
    assert all(r.status == "closed" for r in det.runs)
    assert det.runs[0].pattern == det.runs[1].pattern
    load, = det.loads(0)
    assert load["inrush_w"] == 2600 and load["center_w"] == 1000 and load["n"] == 2
    assert Patterns(tmp_path / "settings.db").all()[0]["inrush_w"] == 2600
    det.patterns.name(load["id"], "Motor")
    det.patterns.assign([])
    assert det.loads(0)[0]["inrush_w"] is None


def test_sql_one_second_means_match_raw_windows_with_gaps_and_invalid_legs(tmp_path):
    ds, rs = motor(start=30.5, stop=130.5)
    ds2, rs2 = motor(start=30.5, stop=130.5, generation=1)
    rs = [r for r in rs if r["ts"] not in (20, 40)] + rs2
    rs[21]["l2_w"] = None
    rs.append(dict(rs[25], frame=rs[25]["frame"] + 30, l1_w=250))
    db = tmp_path / "data.db"
    store_rows(db, ds + ds2, rs)
    deltas = load_deltas(db, 0)
    sql = load_startup_readings(db, deltas)
    actual = detect_runs(deltas, 141, load_readings(db, 0), startup_readings=sql)
    expected = detect_runs(deltas, 141, rs)
    assert [r.as_dict(141) for r in actual] == [r.as_dict(141) for r in expected]
    # The unrelated missing leg must not invalidate L1's baseline.
    assert actual[0].inrush_w == 2200
    for gen in (0, 1):
        a = StartupPower([r for r in sql if r["generation"] == gen])
        b = StartupPower([r for r in rs if r["generation"] == gen])
        for legs in ("L1", "L2", "both"):
            assert a.mean(18.5 * 60, 28.5 * 60, legs) == b.mean(18.5 * 60, 28.5 * 60, legs)


def test_legacy_settings_migrate_without_losing_names(tmp_path):
    db = tmp_path / "settings.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE patterns (
            id INTEGER PRIMARY KEY, legs TEXT, stages INTEGER, center_w REAL, n INTEGER,
            name TEXT, ignored INTEGER, created REAL, named_at REAL, last_seen REAL)""")
        conn.execute("INSERT INTO patterns VALUES (7,'L1',1,1000,1,'Pump',0,0,0,0)")
    p = Patterns(db)
    row, = p.all()
    assert (row["id"], row["name"], row["center_w"], row["inrush_w"]) == (7, "Pump", 1000, None)


def test_delta_only_database_falls_back(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE deltas (generation, start_frame, frame, ts, channel, w)")
        conn.execute("INSERT INTO deltas VALUES (0,1800,1800,30,0,2200)")
    det = Detector(db, tmp_path / "settings.db")
    det.recompute(100)
    assert det.runs[0].w == 2200 and det.runs[0].inrush_w is None
