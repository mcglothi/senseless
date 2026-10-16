"""Upgrade identities and sustained-power regressions from the inrush review."""

import sqlite3

import pytest

from senseless.detect import Detector, Patterns, Run, detect_runs
from senseless.store import Store


def delta(t, w, ch=1):
    return dict(generation=0, start_frame=int(t * 60), frame=int(t * 60),
                ts=t, channel=ch, w=w)


def readings(stop, level):
    return [dict(generation=0, frame=t * 60, ts=t, l1_w=level(t)[0], l2_w=level(t)[1])
            for t in range(stop + 1)]


@pytest.mark.parametrize("ignored", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_upgrade_recentres_pool_identity_before_nearby_named_heater_can_absorb_runs(tmp_path, ignored, legacy):
    settings = tmp_path / "settings.db"
    patterns = Patterns(settings)
    # Exactly the production shape: an L2 pattern named from the 2230 W surge,
    # with an unrelated named 1000 W appliance already in the same settings DB.
    old = [Run(t * 60, t, "L2", w, [(0, w)])
           for t, w in ((30, 2230), (230, 2230), (430, 2230), (630, 1000))]
    patterns.assign(old)
    pool, _, _, heater = [r.pattern for r in old]
    if ignored:
        patterns.ignore(pool)
    else:
        patterns.name(pool, "Pool pump")
    patterns.name(heater, "Heater")
    if legacy:
        # Pre-upgrade settings saved centres/names but no per-run associations.
        with sqlite3.connect(settings) as conn:
            conn.execute("DROP TABLE IF EXISTS pattern_runs")

    ds = [delta(t, w) for t, w in
          ((30, 2230), (130, -945), (230, 2230), (330, -966),
           (430, 2230), (530, -1050), (630, 1000), (730, -1000))]
    rs = readings(740, lambda t: (100, 100 +
                    (2230 if 30 <= t < 33 or 230 <= t < 233 or 430 <= t < 433 else
                     900 if 30 <= t < 130 else 920 if 230 <= t < 330 else
                     1000 if 430 <= t < 530 or 630 <= t < 730 else 0)))
    data = tmp_path / "data.db"
    Store(data).close()
    with sqlite3.connect(data) as conn:
        conn.executemany("INSERT INTO deltas (generation,start_frame,frame,ts,channel,w) "
                         "VALUES (:generation,:start_frame,:frame,:ts,:channel,:w)", ds)
        conn.executemany("INSERT INTO readings (generation,frame,ts,l1_w,l2_w) "
                         "VALUES (:generation,:frame,:ts,:l1_w,:l2_w)", rs)
    for _ in range(2):  # restart/recompute must retain the migration and identities
        det = Detector(data, settings)
        det.recompute(740)
        assert [r.w for r in det.runs] == [900, 920, 1000, 1000]
        # The third pump run is an exact wattage match to Heater. Membership
        # must win even after the two centres have both been migrated/loaded.
        assert [r.pattern for r in det.runs] == [pool, pool, pool, heater]
        assert all(r.status == "closed" for r in det.runs)
        pats = {p["id"]: p for p in det.patterns.all()}
        assert pats[pool]["center_w"] == 920
        assert pats[pool]["n"] == 3 and pats[pool]["inrush_w"] == 2230
        assert pats[pool]["ignored"] == int(ignored)
        assert pats[pool]["name"] == (None if ignored else "Pool pump")
        assert (pats[heater]["name"], pats[heater]["center_w"], pats[heater]["n"]) == ("Heater", 1000, 1)
    future = Run(830 * 60, 830, "L2", 910, [(0, 2230)], inrush_w=2230)
    Patterns(settings).assign([*det.runs, future])
    assert future.pattern == pool
    Patterns(settings).assign([])
    with sqlite3.connect(settings) as conn:
        conn.execute("DELETE FROM pattern_runs")  # history has rolled out of the window
    later = Run(86430 * 60, 86430, "L2", 900, [(0, 2230)], inrush_w=2230)
    Patterns(settings).assign([later])
    assert later.pattern == pool


@pytest.mark.parametrize("w", [100, 150, 287.3])
def test_small_load_return_to_baseline_cannot_close_against_late_unrelated_off(w):
    ds = [delta(30, w if w < 200 else 764.2), delta(200, -w)]
    rs = readings(210, lambda t: (0, 1000 + (w if 30 <= t < 80 or 170 <= t < 200 else 0)))
    run, = detect_runs(ds, 210, rs)
    if w == 287.3:
        assert run.w == pytest.approx(w) and run.inrush_w == 764.2
    assert run.status == "unresolved" and run.end_frame is None
    assert run.as_dict(210)["wh"] == 0


def test_nonoverlapping_small_loads_cannot_share_unrelated_summed_off():
    ds = [delta(30, 150), delta(100, 100), delta(200, -250)]
    rs = readings(210, lambda t: (0, 1000 + (150 if 30 <= t < 70 else
                                          100 if 100 <= t < 140 else
                                          250 if 170 <= t < 200 else 0)))
    runs = detect_runs(ds, 210, rs)
    assert all(r.end_frame is None and r.closed_by is None for r in runs)
    assert all(r.status == "unresolved" and r.as_dict(210)["wh"] == 0 for r in runs)


def test_small_shared_off_needs_summed_overlap_even_when_each_run_is_supported():
    ds = [delta(30, 150), delta(100, 100), delta(200, -250)]
    rs = readings(210, lambda t: (0, 1000 if t < 30 else 1150 if t < 100 else 1100))
    # Keep the newer run on its absolute fallback: 1100 W supports each
    # individual floor, but cannot support their sum above the older baseline.
    rs = [r for r in rs if not 88 <= r["ts"] < 98]
    runs = detect_runs(ds, 210, rs)
    assert all(r.end_frame is None and r.closed_by is None for r in runs)


def test_unassociated_surge_cannot_inherit_a_nearby_named_appliance(tmp_path):
    pats = Patterns(tmp_path / "settings.db")
    heater = pats.find_or_create("L2", 1, 1000)
    pats.name(heater["id"], "Heater")
    motor = Run(1800, 30, "L2", 1000, [(0, 2230)], inrush_w=2230)
    pats.assign([motor])
    assert motor.pattern != heater["id"]
    assert next(p for p in pats.all() if p["id"] == heater["id"])["n"] == 0


@pytest.mark.parametrize("background_start,dip_start", [(0, 60), (60, 120)])
def test_confirmed_off_cannot_hide_earlier_dips_or_remove_a_later_start(background_start, dip_start):
    background_stop = 80 if background_start == 0 else 100
    ds = [delta(background_start, 2000), delta(30, 1000),
          delta(background_stop, -2000), delta(200, -1000)]
    rs = readings(210, lambda t: (0, 5000 + 2000 * (background_start <= t < background_stop)
                                 + 1000 * (30 <= t < dip_start or 170 <= t < 200)))
    runs = detect_runs(ds, 210, rs)
    background = next(r for r in runs if r.w == 2000)
    candidate = next(r for r in runs if r.w == 1000)
    assert background.status == "closed" and background.end_ts == background_stop
    assert candidate.status == "unresolved" and candidate.end_frame is None


@pytest.mark.parametrize("legs", ["L1", "L2", "both"])
def test_confirmed_background_off_preserves_genuine_overlapping_load(legs):
    channels = (0, 1) if legs == "both" else (0,) if legs == "L1" else (1,)
    ds = [delta(t, w / len(channels), ch) for t, w in
          ((0, 2000), (30, 1000), (80, -2000), (200, -1000)) for ch in channels]
    def level(t):
        active = 2000 * (0 <= t < 80) + 1000 * (30 <= t < 200)
        return tuple(100 + (active / len(channels) if ch in channels else 0) for ch in (0, 1))
    rs = [dict(r, frame=r["frame"] + 20 * 60) for r in readings(210, level)]
    # Include covered power before t=0 while keeping frame counters nonnegative.
    rs = readings(19, lambda t: (100, 100)) + rs
    for d in ds:
        d["start_frame"] += 20 * 60
        d["frame"] += 20 * 60
    first, second = detect_runs(ds, 210, rs)
    assert first.status == second.status == "closed"
    assert [r.end_ts for r in (first, second)] == [80, 200]
    assert [r.duration_s(210) for r in (first, second)] == [80, 170]


@pytest.mark.parametrize("start_legs,off_legs", [("L1", "both"), ("L2", "both"),
                                                ("both", "L1"), ("both", "L2")])
def test_startup_overlap_guard_tracks_shared_conductors(start_legs, off_legs):
    start_channels = (0, 1) if start_legs == "both" else (0,) if start_legs == "L1" else (1,)
    off_channels = (0, 1) if off_legs == "both" else (0,) if off_legs == "L1" else (1,)
    ds = [delta(t, w / len(start_channels), ch) for t, w in ((30, 1500), (130, -1500))
          for ch in start_channels]
    background = 2000 if off_legs == "both" else 1000
    ds += [delta(t, w / len(off_channels), ch) for t, w in ((0, background), (40, -background))
           for ch in off_channels]
    def level(t):
        return tuple(100 + (1500 / len(start_channels) if 30 <= t < 130 and ch in start_channels else 0)
                     + (background / len(off_channels) if t < 40 and ch in off_channels else 0)
                     for ch in (0, 1))
    _, run = detect_runs(ds, 140, readings(140, level))
    assert run.w == 1500 and run.inrush_w is None
    assert run.status == "closed" and run.end_ts == 130
