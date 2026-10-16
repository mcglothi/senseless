"""Synthetic duration histories; no captured account data."""

import sqlite3

import pytest

from senseless.detect import (
    Detector, Run, resolve_closed_runs, resolve_open_runs,
)


def history(durations, *, pattern=1, legs="L1", w=56):
    runs = []
    start = 0
    for duration in durations:
        runs.append(Run(start * 60, start, legs, w, [(0, w)],
                        end_frame=(start + duration) * 60, end_ts=start + duration,
                        off_w=-w, pattern=pattern))
        start += duration + 10
    return runs


@pytest.mark.parametrize("legs", ["L1", "L2", "both"])
def test_seconds_long_repeater_cannot_have_a_four_hour_run(legs):
    runs = history([13] * 40 + [14741], legs=legs)
    resolve_closed_runs(runs, 20000, set())
    assert all(r.status == "closed" for r in runs[:-1])
    bad = runs[-1]
    assert bad.status == "unresolved"
    assert bad.end_frame is bad.end_ts is bad.off_w is None
    assert bad.duration_s(20000) == bad.as_dict(20000)["wh"] == 0
    resolve_open_runs(runs, bad.start_ts + 1)
    assert bad.status == "unresolved"  # open-run resolution must not revive it


def test_repeated_passes_remove_outliers_that_mask_one_another():
    runs = history([13] * 8 + [1000, 20000])
    resolve_closed_runs(runs, 30000, set())
    assert [r.status for r in runs] == ["closed"] * 8 + ["unresolved"] * 2
    resolve_closed_runs(runs, 30000, set())
    assert [r.status for r in runs] == ["closed"] * 8 + ["unresolved"] * 2


@pytest.mark.parametrize("durations", [
    [1200, 1800, 2400, 3600, 5400] * 4,  # normal freezer/blower history
    [13] * 6 + [14741],                 # too few closed examples
    [13] * 7 + [240],                   # less than 20x the median
    [13] * 7 + [1000, 3000],            # upper tail supports the long runs
])
def test_long_or_sparse_or_broad_histories_keep_their_closures(durations):
    runs = history(durations)
    resolve_closed_runs(runs, 100000, set())
    assert all(r.status == "closed" and r.end_frame is not None for r in runs)


def test_named_patterns_and_unassigned_runs_are_exempt():
    named = history([13] * 40 + [14741])
    unassigned = history([13] * 40 + [14741], pattern=None)
    resolve_closed_runs(named + unassigned, 100000, {1})
    assert all(r.status == "closed" for r in named + unassigned)


def test_other_patterns_and_unresolved_runs_do_not_supply_samples():
    sparse = history([13] * 6 + [14741])
    other = history([13] * 40, pattern=2, legs="L2")
    unresolved = history([13] * 40)
    for r in unresolved:
        r.end_frame = r.end_ts = r.off_w = None
        r.status = "unresolved"
    resolve_closed_runs(sparse + other + unresolved, 100000, set())
    assert sparse[-1].status == "closed"
    assert all(r.status == "unresolved" for r in unresolved)


def test_eight_closed_examples_suffice_without_self_polluted_percentile():
    runs = history([13] * 7 + [14741])
    resolve_closed_runs(runs, 20000, set())
    assert runs[-1].status == "unresolved"


def test_detector_rejects_after_assignment_and_keeps_named_patterns(tmp_path):
    from senseless.store import Store
    db = tmp_path / "data.db"
    Store(db).close()
    examples = history([13] * 10 + [14741])
    with sqlite3.connect(db) as conn:
        for r in examples:
            conn.executemany(
                "INSERT INTO deltas (generation,start_frame,frame,ts,channel,w) VALUES (0,?,?,?,?,?)",
                [(r.start_frame, r.start_frame, r.start_ts, 0, r.w),
                 (r.end_frame, r.end_frame, r.end_ts, 0, r.off_w)],
            )
    det = Detector(db, tmp_path / "settings.db")
    now = examples[-1].end_ts + 1
    det.recompute(now)
    assert det.runs[-1].status == "unresolved"
    load, = det.loads(0)
    assert load["closed_runs"] == 10 and load["median_duration_s"] == 13
    assert load["wh_window"] == pytest.approx(56 * 130 / 3600, abs=.1)
    assert det.runs_between(examples[-1].start_ts + 1, now) == []
    pid = load["id"]
    det.patterns.name(pid, "Known long load")
    det.recompute(now)
    assert det.runs[-1].status == "closed" and det.runs[-1].pattern == pid
    assert det.patterns.all()[0]["name"] == "Known long load"
