"""Adversarial checks of notes/detection.md, including its stated ambiguities.

Synthetic data only. Randomized checks use fixed seeds and no extra dependencies.
"""

import itertools
import random
import sqlite3
import time

import pytest

from senseless.detect import (
    Detector, MAX_RUN_S, Patterns, Run, detect_runs, load_sense_ons,
    pair_runs, resolve_open_runs, sense_hints, to_steps,
)


def delta(frame, w, channel=0, *, ts=None, generation=0):
    return dict(start_frame=frame, channel=channel, w=w,
                ts=frame / 60 if ts is None else ts, generation=generation)


def run(w=100, ts=100, *, legs="L1", pattern=1, stages=None):
    return Run(int(ts * 60), ts, legs, w,
               stages if stages is not None else [(0, w)], pattern=pattern,
               status="running")


def database(path, deltas=(), devices=()):
    """Minimal real SQLite input for exercising the complete recompute path."""
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE deltas (generation INTEGER, start_frame INTEGER,
                                 channel INTEGER, w REAL, ts REAL);
            CREATE INDEX deltas_ts ON deltas(ts);
            CREATE TABLE device_power (ts REAL, device_id TEXT, name TEXT, w REAL);
        """)
        db.executemany("INSERT INTO deltas VALUES (:generation,:start_frame,:channel,:w,:ts)", deltas)
        db.executemany("INSERT INTO device_power VALUES (?,?,?,?)", devices)


@pytest.mark.parametrize("channel", [0, 1])
def test_distinct_interleaved_loads_all_on_and_off_orders(channel):
    # Gaps exceed STAGE_FRAMES; sizes cannot pair with each other's offs.
    for ons in itertools.permutations((100, 400, 1600)):
        for offs in itertools.permutations((100, 400, 1600)):
            ds = [delta(i * 300, w, channel) for i, w in enumerate(ons)]
            ds += [delta(900 + i * 60, -w, channel) for i, w in enumerate(offs)]
            runs = detect_runs(ds, 100)
            assert len(runs) == 3
            for r in runs:
                assert r.start_frame == ons.index(r.w) * 300
                assert r.end_frame == 900 + offs.index(r.w) * 60
                assert r.off_w == -r.w and r.status == "closed"


def test_seeded_interleavings_match_a_most_recent_compatible_oracle():
    rng = random.Random(6137)
    for _ in range(100):
        events = []
        active = []
        expected = {}
        # Frequent identical and near-identical sizes deliberately create ambiguity.
        for i in range(80):
            frame = i * 180
            w = rng.choice((100, 110, 400, 420, 1600))
            if rng.random() < 0.55:
                events.append(delta(frame, w))
                active.append((frame, w))
                expected[frame] = None
            else:
                events.append(delta(frame, -w))
                for j in range(len(active) - 1, -1, -1):
                    start, size = active[j]
                    if abs(size - w) <= max(0.15 * size, 20):
                        expected[start] = frame
                        active.pop(j)
                        break
        rng.shuffle(events)  # to_steps promises unordered input support
        runs = detect_runs(events, 240)
        assert {r.start_frame: r.end_frame for r in runs} == expected


def test_identical_overlapping_loads_close_in_lifo_order():
    ds = [delta(0, 500), delta(121, 500), delta(242, 500),
          delta(500, -500), delta(501, -500), delta(502, -500)]
    runs = detect_runs(ds, 10)
    assert [(r.start_frame, r.end_frame) for r in runs] == [(0, 502), (121, 501), (242, 500)]


def test_close_unrelated_ons_merge_as_the_documented_limitation():
    runs = detect_runs([delta(0, 100), delta(120, 400),
                        delta(600, -100), delta(660, -400)], 12)
    assert len(runs) == 1
    assert runs[0].w == 500 and runs[0].stages == [(0, 100), (120, 400)]
    # Neither off is compatible with the combined on; offs must not merge.
    assert runs[0].running and runs[0].end_frame is None


@pytest.mark.parametrize("extra_first", [False, True])
def test_240v_and_single_leg_same_frame_respect_first_delta_rule(extra_first):
    # When the unrelated L1 delta comes first, the documented heuristic misses
    # the balanced pair. Do not require physical identity beyond that rule.
    l1 = [delta(0, 100), delta(0, 1000)] if extra_first else [delta(0, 1000), delta(0, 100)]
    ds = l1 + [delta(0, 1000, 1)]
    if extra_first:
        assert [(s.legs, s.w) for s in to_steps(ds)] == [("L1", 1100), ("L2", 1000)]
    else:
        ds += [delta(600, -1000), delta(600, -1000, 1), delta(600, -100)]
        runs = detect_runs(ds, 20)
        assert [(r.legs, r.w, r.end_frame) for r in runs] == [("both", 2000, 600), ("L1", 100, 600)]


def test_offs_before_ons_are_discarded_even_with_shuffled_input():
    ds = [delta(0, -500), delta(1, -500), delta(300, 500), delta(600, -500), delta(900, 500)]
    random.Random(9).shuffle(ds)
    runs = detect_runs(ds, 20)
    assert [(r.start_frame, r.end_frame, r.status) for r in runs] == [(300, 600, "closed"), (900, None, "running")]


def test_generation_reset_does_not_pair_merge_or_balance_across_generations():
    ds = [delta(0, 1000, ts=100, generation=0),
          delta(600, -1000, ts=110, generation=1),
          delta(0, 1000, 1, ts=100, generation=1),
          delta(90, 400, ts=101.5, generation=2),
          delta(690, -400, ts=111.5, generation=2)]
    random.Random(17).shuffle(ds)
    runs = detect_runs(ds, 120)
    assert [(r.generation, r.legs, r.w, r.status) for r in runs] == [
        (0, "L1", 1000, "running"), (1, "L2", 1000, "running"), (2, "L1", 400, "closed")]
    assert runs[-1].duration_s(120) == 10
    assert [(r.generation, r.status) for r in detect_runs(ds, 100 + MAX_RUN_S + 1)] == [(2, "closed")]


@pytest.mark.parametrize("w,kept", [(29.999, False), (30, True), (-29.999, False), (-30, True)])
def test_noise_threshold_is_inclusive_and_signed(w, kept):
    steps = to_steps([delta(0, w)])
    assert len(steps) == int(kept)
    if kept:
        assert steps[0].w == w


def test_noise_is_filtered_before_balance_and_stage_merging():
    assert [(s.legs, s.w) for s in to_steps([delta(0, 30), delta(0, 29.999, 1), delta(1, 29.999)])] == [("L1", 30)]


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("small,both", [(750, True), (749.999, False)])
def test_240v_tolerance_boundary(sign, small, both):
    steps = to_steps([delta(0, sign * 1000), delta(0, sign * small, 1)])
    assert [s.legs for s in steps] == (["both"] if both else ["L1", "L2"])
    assert sum(s.w for s in steps) == pytest.approx(sign * (1000 + small))


def test_opposite_sign_balanced_steps_do_not_form_240v():
    assert [(s.legs, s.w) for s in to_steps([delta(0, 1000), delta(0, -1000, 1)])] == [("L1", 1000), ("L2", -1000)]


@pytest.mark.parametrize("gap,merged", [(119, True), (120, True), (121, False)])
def test_stage_frame_boundary_and_chained_gaps(gap, merged):
    steps = to_steps([delta(0, 100), delta(gap, 200), delta(2 * gap, 300)])
    if merged:
        assert len(steps) == 1 and steps[0].w == 600
        assert steps[0].stages == [(0, 100), (gap, 200), (2 * gap, 300)]
    else:
        assert [s.w for s in steps] == [100, 200, 300]


@pytest.mark.parametrize("on,off,closed", [
    (100, 80, True), (100, 120, True), (100, 79.999, False), (100, 120.001, False),
    (1000, 850, True), (1000, 1150, True), (1000, 849.999, False), (1000, 1150.001, False),
])
def test_off_absolute_and_relative_tolerance_boundaries(on, off, closed):
    runs = detect_runs([delta(0, on), delta(600, -off)], 20)
    assert len(runs) == 1
    assert (runs[0].end_frame == 600) is closed
    assert runs[0].running is not closed


@pytest.mark.parametrize("age,kept", [(MAX_RUN_S - .001, True), (MAX_RUN_S, True), (MAX_RUN_S + .001, False)])
def test_max_run_age_for_open_and_closed_runs(age, kept):
    ds = [delta(0, 100, ts=100)]
    assert len(detect_runs(ds, 100 + age)) == int(kept)
    ds.append(delta(round(age * 60), -100, ts=100 + age))
    runs = detect_runs(ds, 100 + age)
    assert len(runs) == int(kept)
    if kept:
        assert runs[0].status == "closed"


def test_closed_duration_uses_frames_while_pairing_age_uses_timestamps():
    # Irregular delivery timestamps must not stretch a closed run's energy.
    r, = detect_runs([delta(0, 100, ts=100), delta(600, -100, ts=1000)], 1001)
    assert r.duration_s(1001) == 10
    assert r.as_dict(1001)["wh"] == round(100 * 10 / 3600, 2)
    # Conversely, a small frame gap cannot rescue a timestamp-expired on.
    assert detect_runs([delta(0, 100, ts=100),
                        delta(600, -100, ts=101 + MAX_RUN_S)], 101 + MAX_RUN_S) == []


@pytest.mark.parametrize("age,status", [(600, "running"), (600.001, "unresolved")])
def test_no_examples_open_status_boundary(age, status):
    r = run(ts=0)
    resolve_open_runs([r], age)
    assert r.status == status
    assert r.duration_s(age) == (age if status == "running" else 0)
    assert r.as_dict(age)["wh"] == round(r.w * r.duration_s(age) / 3600, 2)


@pytest.mark.parametrize("durations,limit", [([1, 2], 30), ([10, 20], 60)])
def test_status_uses_upper_median_and_thirty_second_floor(durations, limit):
    closed = [Run(0, 0, "L1", 100, [(0, 100)], end_frame=int(d * 60), pattern=1) for d in durations]
    r = run(ts=100)
    resolve_open_runs(closed + [r], 100 + limit)
    assert r.status == "running"
    resolve_open_runs(closed + [r], 100 + limit + .001)
    assert r.status == "unresolved" and r.duration_s(1e6) == 0


def test_empty_inputs_and_empty_database_recompute(tmp_path):
    assert to_steps([]) == pair_runs([], 0) == detect_runs([], 0) == []
    assert sense_hints([], []) == sense_hints([], [dict(ts=0, name="Lamp", w=100)]) == {}
    assert resolve_open_runs([], 0) is None
    path = tmp_path / "input.db"
    database(path)
    det = Detector(path, tmp_path / "settings.db")
    det.recompute(100)
    assert det.runs == [] and det.hints == {} and det.loads(0) == []
    assert det.patterns.all() == [] and det.computed_at == 100


def test_repeated_assignments_and_restarts_freeze_named_centres(tmp_path):
    path = tmp_path / "settings.db"
    p = Patterns(path)
    p.assign([run(1000)])
    pid = p.all()[0]["id"]
    p.name(pid, " Heater ")
    rng = random.Random(1984)
    for _ in range(30):
        runs = [run(rng.randint(890, 1110), ts=100 + i) for i in range(25)]
        p = Patterns(path)
        p.assign(runs)
        assert {r.pattern for r in runs} == {pid}
        row, = p.all()
        assert (row["center_w"], row["name"], row["n"], row["last_seen"]) == (1000, "Heater", 25, 124)
    p.assign([])
    row, = p.all()
    assert row["center_w"] == 1000 and row["n"] == 0


def test_identical_windows_do_not_duplicate_patterns_or_accumulate_counts(tmp_path):
    p = Patterns(tmp_path / "settings.db")
    expected_ids = None
    for _ in range(20):
        runs = [run(w, ts=100 + i, legs=leg, stages=[(j * 90, w / n) for j in range(n)])
                for i, (w, leg, n) in enumerate(itertools.product((100, 1000), ("L1", "L2", "both"), (1, 2, 3)))]
        p.assign(runs)
        ids = [r.pattern for r in runs]
        if expected_ids is None:
            expected_ids = ids
        assert ids == expected_ids
        assert len(p.all()) == 12  # categories are one vs. multiple stages
        assert sorted(row["n"] for row in p.all()) == [1] * 6 + [2] * 6


@pytest.mark.parametrize("w,joins", [(880, True), (1120, True), (879.999, False), (1120.001, False)])
def test_cluster_tolerance_boundary_with_frozen_centre(tmp_path, w, joins):
    p = Patterns(tmp_path / "settings.db")
    first = run(1000)
    p.assign([first])
    p.name(first.pattern, "Reference")
    other = run(w)
    p.assign([other])
    assert (other.pattern == first.pattern) is joins
    assert len(p.all()) == (1 if joins else 2)


def test_assignment_uses_nearest_existing_centre_then_refreshes_upper_median(tmp_path):
    p = Patterns(tmp_path / "settings.db")
    seeds = [run(1000), run(1200)]
    p.assign(seeds)
    rs = [run(1110), run(1120), run(1190)]
    p.assign(rs)
    assert {r.pattern for r in rs} == {seeds[1].pattern}
    assert next(row for row in p.all() if row["id"] == seeds[1].pattern)["center_w"] == 1120


def test_clearing_name_allows_centre_updates_again(tmp_path):
    p = Patterns(tmp_path / "settings.db")
    initial = run(1000)
    p.assign([initial])
    p.name(initial.pattern, "Frozen")
    p.assign([run(1100)])
    assert p.all()[0]["center_w"] == 1000
    p.name(initial.pattern, "   ")
    p.assign([run(1100)])
    row, = p.all()
    assert row["center_w"] == 1100 and row["name"] is None


@pytest.mark.parametrize("offset,w,accepted", [
    (-10, 1000, True), (30, 1000, True), (-10.001, 1000, False), (30.001, 1000, False),
    (0, 600, True), (0, 599.999, False), (0, 2000, False),
])
def test_hint_time_and_size_boundaries(offset, w, accepted):
    runs = [run(1000, ts=t) for t in (100, 1000)]
    ons = [dict(ts=t + offset, name="Heater", w=w) for t in (100, 1000)]
    assert sense_hints(runs, ons) == ({1: dict(name="Heater", matches=2, share=1.0)} if accepted else {})


def test_hint_share_threshold_and_one_on_cannot_supply_two_matches():
    runs = [run(ts=t) for t in (100, 200, 300, 400, 500)]
    ons = [dict(ts=t, name="Lamp", w=100) for t in (100, 200)]
    assert sense_hints(runs, ons) == {1: dict(name="Lamp", matches=2, share=.4)}
    assert sense_hints(runs + [run(ts=600)], ons) == {}
    assert sense_hints([run(ts=100), run(ts=101)], [dict(ts=100, name="Lamp", w=100)]) == {}


def test_sense_hints_many_devices_and_duplicate_updates():
    runs = [run(1000, ts=100 + i * 100, pattern=7) for i in range(10)]
    ons = []
    for r in runs:
        ons += [dict(ts=r.start_ts + j / 100, name=f"Noise {j}", w=50) for j in range(300)]
        # Repeated activations of one name may only vote once for this run.
        ons += [dict(ts=r.start_ts + j, name="Heater", w=1000) for j in (1, 2, 3)]
    random.Random(81).shuffle(ons)
    random.Random(82).shuffle(runs)
    assert sense_hints(runs, ons) == {7: dict(name="Heater", matches=10, share=1.0)}
    assert {r.pattern for r in runs} == {7}  # suggestions do not rename/reassign


def test_hint_activation_is_given_to_nearest_run_not_first_run():
    # Two isolated repetitions reach the normal two-match suggestion threshold.
    runs = [run(ts=t, pattern=p) for t, p in ((100, 1), (120, 2), (1000, 1), (1020, 2))]
    ons = [dict(ts=t, name="Lamp", w=100) for t in (125, 1025)]
    assert sense_hints(runs, ons) == {2: dict(name="Lamp", matches=2, share=1.0)}


def test_sense_peak_uses_entire_thirty_seconds_not_row_count(tmp_path):
    path = tmp_path / "input.db"
    # The 41st changed reading is the first excluded by the hidden row cap.
    rows = [(i / 2, "lamp", "Lamp", 100 + i if i < 40 else 1000) for i in range(41)]
    database(path, devices=rows)
    assert load_sense_ons(path, 0) == [dict(ts=0, name="Lamp", w=1000)]


def test_sense_peak_stops_at_off_next_device_and_thirty_second_boundary(tmp_path):
    path = tmp_path / "input.db"
    database(path, devices=[(0, "a", "A", 20), (30, "a", "A", 100),
                            (30.001, "a", "A", 900), (31, "a", "A", 0),
                            (32, "a", "A", 500), (0, "b", "B", 800),
                            (0, "c", "Always On", 2000), (0, "d", "Other", 3000)])
    assert load_sense_ons(path, 0) == [dict(ts=0, name="A", w=100), dict(ts=32, name="A", w=500), dict(ts=0, name="B", w=800)]


def test_recompute_fifty_thousand_deltas_under_three_seconds(tmp_path):
    path = tmp_path / "input.db"
    ds = []
    for i in range(25_000):
        ds += [delta(i * 360, 100), delta(i * 360 + 180, -100)]
    database(path, ds)
    det = Detector(path, tmp_path / "settings.db")
    start = time.perf_counter()
    det.recompute(now_ts=150_000)
    elapsed = time.perf_counter() - start
    assert len(det.runs) == 25_000
    assert all(r.status == "closed" and r.duration_s(0) == 3 for r in det.runs)
    row, = det.patterns.all()
    assert row["n"] == 25_000 and row["center_w"] == 100
    assert elapsed < 3, f"50k deltas recomputed in {elapsed:.3f}s"


def test_recompute_fifty_thousand_unmatched_deltas_under_three_seconds(tmp_path):
    path = tmp_path / "input.db"
    # Each unmatched off interrupts stages but closes nothing. All ons are
    # younger than MAX_RUN_S: this stresses the growing candidate list rather
    # than merely benchmarking sorting/SQLite on a perfectly paired stream.
    ds = [delta(i, 100 if i % 2 == 0 else -40) for i in range(50_000)]
    database(path, ds)
    det = Detector(path, tmp_path / "settings.db")
    start = time.perf_counter()
    det.recompute(now_ts=50_000 / 60)
    elapsed = time.perf_counter() - start
    assert len(det.runs) == 25_000
    assert all(r.end_frame is None and r.w == 100 for r in det.runs)
    row, = det.patterns.all()
    assert row["n"] == 25_000
    assert elapsed < 3, f"50k unmatched deltas recomputed in {elapsed:.3f}s"
