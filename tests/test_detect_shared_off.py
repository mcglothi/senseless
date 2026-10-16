"""Shared timer offs, using synthetic steps and power only."""

import pytest

from senseless import detect
from senseless.detect import MAX_RUN_S, SHARED_OFF_CAP, Step, pair_runs


def step(t, w, legs="L2"):
    return Step(int(t * 60), t, legs, w)


def power(stop, level):
    return [dict(frame=t * 60, ts=t, l1_w=0, l2_w=level(t)) for t in range(stop + 1)]


@pytest.mark.parametrize("legs", ["L1", "L2", "both"])
def test_two_load_timer_closes_both_and_serializes_shared_off(legs):
    runs = pair_runs([step(0, 900, legs), step(55, 280, legs), step(3600, -1054, legs)], 3601)
    assert len(runs) == 2
    for run, duration in zip(runs, (3600, 3545)):
        assert run.status == "closed" and run.end_ts == 3600 and run.off_w == -1054
        assert run.duration_s(3601) == duration
        assert run.as_dict(3601)["closed_by"] == "shared-off"
        assert run.as_dict(3601)["wh"] == round(run.w * duration / 3600, 2)


def test_single_matching_run_wins_even_when_pair_is_more_exact():
    single, a, b = pair_runs([step(0, 1000), step(10, 700), step(20, 350), step(100, -1050)], 101)
    assert single.end_ts == 100 and single.closed_by is None
    assert a.end_ts is b.end_ts is None


@pytest.mark.parametrize("low", [500, 950])
def test_power_must_support_each_run_and_the_combined_overlap(low):
    # 950 W supports each individual load but contradicts their sum's 1003 W floor.
    rs = power(110, lambda t: low if 40 <= t < 70 else 1300)
    runs = pair_runs([step(0, 900), step(25, 280), step(100, -1054)], 110, rs,
                     startup_readings=[])
    assert all(r.end_ts is None and r.closed_by is None for r in runs)


def test_supported_shared_off_with_power_readings():
    rs = power(110, lambda t: 1000 if t < 25 else 1300 if t < 100 else 50)
    runs = pair_runs([step(0, 900), step(25, 280), step(100, -1054)], 110, rs,
                     startup_readings=[])
    assert all(r.end_ts == 100 and r.closed_by == "shared-off" for r in runs)


def test_pre_overlap_evidence_rejects_old_run_even_if_sum_is_supported_later():
    rs = power(110, lambda t: 100 if 10 <= t < 20 else 1300)
    runs = pair_runs([step(0, 900), step(40, 280), step(100, -1054)], 110, rs,
                     startup_readings=[])
    assert all(r.end_ts is None for r in runs)


def test_closest_starts_win_before_smallest_error():
    runs = pair_runs([step(0, 700), step(50, 301), step(60, 650), step(100, -1001)], 101)
    assert [r.end_ts for r in runs] == [None, 100, 100]


def test_smallest_error_breaks_equal_start_gap_then_most_recent_wins():
    runs = pair_runs([step(0, 700), step(10, 300), step(20, 650), step(100, -1000)], 101)
    assert [r.end_ts for r in runs] == [100, 100, None]
    runs = pair_runs([step(0, 700), step(10, 300), step(20, 700), step(100, -1000)], 101)
    assert [r.end_ts for r in runs] == [None, 100, 100]


@pytest.mark.parametrize("fillers,included", [(SHARED_OFF_CAP - 2, True), (SHARED_OFF_CAP - 1, False)])
def test_cap_counts_only_open_runs_on_the_same_legs(fillers, included):
    steps = [step(0, 700), step(10, 300)]
    steps += [step(20 + i, 40) for i in range(fillers)]
    steps += [step(40, 600, "L1"), step(50, -600, "L1"), step(60, -1000)]
    runs = pair_runs(steps, 61)
    assert (runs[0].end_ts == runs[1].end_ts == 60) is included


def test_closed_newer_runs_do_not_hide_older_candidates_from_cap():
    steps = [step(0, 700), step(10, 300)]
    for i in range(SHARED_OFF_CAP + 1):
        steps += [step(20 + i * 2, 50), step(21 + i * 2, -50)]
    steps.append(step(100, -1000))
    runs = pair_runs(steps, 101)
    assert runs[0].end_ts == runs[1].end_ts == 100


@pytest.mark.parametrize("off,closes", [(MAX_RUN_S, True), (MAX_RUN_S + 1, False)])
def test_shared_off_obeys_max_age(off, closes):
    runs = pair_runs([step(0, 700), step(10, 300), step(off, -1000)], off + 1, keep_expired=True)
    assert all(r.closed_by == "shared-off" for r in runs) is closes


def test_pair_never_crosses_leg_categories_or_generations():
    assert all(r.end_ts is None for r in pair_runs(
        [step(0, 700, "L1"), step(10, 300), step(100, -1000)], 101))
    ds = [dict(start_frame=t * 60, ts=t, w=w, channel=1, generation=g)
          for t, w, g in [(0, 700, 0), (10, 300, 1), (100, -1000, 1)]]
    assert all(r.end_ts is None for r in detect.detect_runs(ds, 101))


def test_absolute_tolerance_and_disabling(monkeypatch):
    steps = [step(0, 30), step(10, 40), step(100, -90)]
    assert all(r.closed_by == "shared-off" for r in pair_runs(steps, 101))
    monkeypatch.setattr(detect, "SHARED_OFF_CAP", 0)
    assert all(r.end_ts is None for r in pair_runs(steps, 101))


def test_rejected_duration_outlier_clears_shared_off_provenance():
    runs = [detect.Run(t * 60, t, "L2", 100, [(0, 100)],
                       end_frame=(t + duration) * 60, end_ts=t + duration,
                       off_w=-200, pattern=1, closed_by="shared-off")
            for t, duration in [(i * 100, 10) for i in range(8)] + [(1000, 1000)]]
    detect.resolve_closed_runs(runs, 2100, set())
    assert runs[-1].status == "unresolved" and runs[-1].closed_by is None
    assert all(r.closed_by == "shared-off" for r in runs[:-1])
