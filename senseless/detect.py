"""Load detection from the monitor's on-board steps (deltas).

Pipeline, recomputed over a sliding window (cheap: a few thousand deltas a day):

1. **Steps**: steps on L1 and L2 in the same AC cycle with the same sign and about the same
   size are one 240 V step (legs "both").
2. **Stages**: same-sign steps on the same legs within STAGE_FRAMES are one multi-stage step
   (a microwave: +299 W, then +1,726 W 90 cycles later).
3. **Runs**: covered pre/settled leg means size surge starts by their running level. An
   "off" step closes the most recent compatible "on" on the same legs (size
   within 15%, or 20% for a settled surge), then tries a bounded pair sharing that off. Sustained leg power can
   disprove either match. Missed offs become unresolved.
4. **Patterns**: runs are grouped by legs + stage count + size; a run joins the nearest known
   pattern centre within CLUSTER_TOL, else starts a new one. Patterns and their names persist in
   settings.db, so names survive recomputes and restarts. Extreme duration outliers in well-sampled,
   unnamed patterns become unresolved.
"""

import json
import math
import sqlite3
import threading
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from statistics import median

MIN_STEP_W = 30          # below this the on-board steps are mostly noise floor
PAIR_240_TOL = 0.25      # L1/L2 size mismatch allowed for one 240 V step
STAGE_FRAMES = 120       # 2 s at 60 Hz (microwave stages: 90 cycles apart)
PAIR_TOL = 0.15          # off step within 15% (or PAIR_ABS_W) of the on step
INRUSH_PAIR_TOL = 0.20   # settled rise is an estimate, unlike a reported plain on
PAIR_ABS_W = 20
SLACK_W = 300           # background uncertainty, also bounded to 30% of the load
SHARED_OFF_CAP = 12      # at most 66 pairs per unmatched off; 0 disables shared offs
SHARED_START_S = 600     # shared timer/strip loads must start within ten minutes
MAX_RUN_S = 6 * 3600
CLUSTER_TOL = 0.12
WINDOW_S = 48 * 3600
PRUNE_AFTER_S = 7 * 86400   # unnamed patterns unseen this long are deleted
FPS = 60
INRUSH_MIN_W = 300
INRUSH_OVERLAP_W = 120
INRUSH_OVERLAP_SHARE = 0.15   # a switch elsewhere only muddies the settled window if it's sizeable next to this load
INRUSH_RATIO = .8
INRUSH_COVERAGE = .7


@dataclass
class Step:
    frame: int
    ts: float
    legs: str            # "L1" | "L2" | "both"
    w: float             # signed total
    stages: list = field(default_factory=list)  # [(frame offset, w)] for multi-stage ons
    leg_w: dict | None = None  # observed split of a both-leg step, for background offs


@dataclass
class Run:
    start_frame: int
    start_ts: float
    legs: str
    w: float             # settled rise for a surge; otherwise reported on-size (positive)
    stages: list
    end_frame: int | None = None
    end_ts: float | None = None
    off_w: float | None = None
    pattern: int | None = None
    # closed: off seen. running: no off yet, still plausible for its pattern. unresolved: no reliable
    # off (missed off or implausible pairing: Sense's on/off steps aren't always symmetric).
    status: str = "closed"
    generation: int = 0
    cycle: str | None = None
    inrush_w: float | None = None  # reported on-size when w was adjusted to the settled rise
    closed_by: str | None = None  # "shared-off" when two runs share one observed off

    @property
    def running(self) -> bool:
        return self.status == "running"

    def duration_s(self, now_ts: float) -> float:
        """Closed: on to off. Running: so far. Unresolved: unknown, counted as 0."""
        if self.end_frame is not None:
            return (self.end_frame - self.start_frame) / FPS
        return max(0.0, now_ts - self.start_ts) if self.status == "running" else 0.0

    def as_dict(self, now_ts: float) -> dict:
        d = self.duration_s(now_ts)
        return {"generation": self.generation, "start_frame": self.start_frame, "start_ts": self.start_ts, "end_frame": self.end_frame,
                "end_ts": self.end_ts, "legs": self.legs, "w": round(self.w, 1), "off_w": self.off_w,
                "inrush_w": round(self.inrush_w, 1) if self.inrush_w is not None else None,
                "closed_by": self.closed_by,
                "stages": self.stages, "duration_s": round(d, 1), "running": self.running, "status": self.status,
                "wh": round(self.w * d / 3600, 2), "pattern": self.pattern, "cycle": self.cycle}


def to_steps(deltas: list[dict]) -> list[Step]:
    """deltas: rows with start_frame, channel (0/1), w, ts — ordered or not."""
    by_frame: dict[int, list[dict]] = {}
    for d in deltas:
        if abs(d["w"]) < MIN_STEP_W:
            continue
        by_frame.setdefault(d["start_frame"], []).append(d)
    steps = []
    for frame in sorted(by_frame):
        group = by_frame[frame]
        a = next((d for d in group if d["channel"] == 0), None)
        b = next((d for d in group if d["channel"] == 1), None)
        if a and b and math.copysign(1, a["w"]) == math.copysign(1, b["w"]) and \
                abs(abs(a["w"]) - abs(b["w"])) / max(abs(a["w"]), abs(b["w"])) <= PAIR_240_TOL:
            steps.append(Step(frame, a["ts"], "both", a["w"] + b["w"],
                              leg_w={"L1": a["w"], "L2": b["w"]}))
            rest = [d for d in group if d is not a and d is not b]
        else:
            rest = group
        for d in rest:
            steps.append(Step(frame, d["ts"], "L1" if d["channel"] == 0 else "L2", d["w"]))
    return merge_stages(steps)


def merge_stages(steps: list[Step]) -> list[Step]:
    """Fold on-steps on the same legs within STAGE_FRAMES of the previous stage into one multi-stage
    step. Steps on other legs in between don't interrupt; an off on the same legs does. Offs never
    merge (two loads turning off a second apart must stay separately pairable)."""
    out: list[Step] = []
    last: dict[str, Step] = {}  # most recent step per legs, either sign
    for s in sorted(steps, key=lambda s: s.frame):
        prev = last.get(s.legs)
        # only ons merge: two loads switching off a second apart must stay two pairable offs
        if prev is not None and prev.w > 0 and s.w > 0:
            prev_end = prev.frame + (prev.stages[-1][0] if prev.stages else 0)
            if s.frame - prev_end <= STAGE_FRAMES:
                if not prev.stages:
                    prev.stages = [(0, round(prev.w, 1))]
                prev.stages.append((s.frame - prev.frame, round(s.w, 1)))
                prev.w += s.w
                continue
        out.append(s)
        last[s.legs] = s
    return out


def _compatible(on: Run, off: Step) -> bool:
    diff = abs(on.w - abs(off.w))
    tol = INRUSH_PAIR_TOL if on.inrush_w is not None else PAIR_TOL
    return diff <= max(tol * on.w, PAIR_ABS_W)


def startup_candidates(steps: list[Step]):
    """Unambiguous >=300 W ons, checking every event on shared conductors.

    Merged startup stages belong to the candidate itself. Their last frame anchors
    the settled window, while the baseline stays before the first stage.
    """
    switching = {leg: [] for leg in ("L1", "L2", "both")}
    for i, s in enumerate(steps):
        if abs(s.w) >= INRUSH_OVERLAP_W:
            for leg in switching:
                if leg == s.legs or "both" in (leg, s.legs):
                    switching[leg].extend((s.frame + offset, i) for offset, _ in (s.stages or [(0, s.w)]))
    for events in switching.values():
        events.sort()
    for i, s in enumerate(steps):
        if s.w < INRUSH_MIN_W:
            continue
        last = s.frame + (s.stages[-1][0] if s.stages else 0)
        stop = last + 20 * FPS
        events = switching[s.legs]
        lo = bisect_right(events, (s.frame, math.inf))
        hi = bisect_right(events, (stop, math.inf))
        # a small switch (a 125 W blip beside a 1.7 kW well pump) doesn't make the settled window ambiguous
        floor = max(INRUSH_OVERLAP_W, INRUSH_OVERLAP_SHARE * s.w)
        if not any(j != i and abs(steps[j].w) >= floor for _, j in events[lo:hi]):
            yield s, last


class StartupPower:
    """One-second leg means with covered-time window averaging, within one generation."""

    def __init__(self, readings: list[dict]):
        if readings and "mean_start" in readings[0]:
            self.bins = readings
        else:
            grouped = {}
            for row in readings:
                if row["frame"] >= 0:
                    grouped.setdefault(row["frame"] // FPS, []).append(row)
            self.bins = []
            for k, rows in sorted(grouped.items()):
                item = {"mean_start": k * FPS}
                for leg, keys in (("L1", ("l1_w",)), ("L2", ("l2_w",)), ("both", ("l1_w", "l2_w"))):
                    values = [sum(r[key] for key in keys) if all(
                        r.get(key) is not None and math.isfinite(r[key]) for key in keys
                    ) else math.inf for r in rows]
                    item[leg] = sum(values) / len(values)
                self.bins.append(item)
        self.frames = [row["mean_start"] for row in self.bins]

    def mean(self, start: int, end: int, legs: str) -> float | None:
        lo = bisect_right(self.frames, start - FPS)
        hi = bisect_left(self.frames, end)
        covered = total = 0.0
        for row in self.bins[lo:hi]:
            value = row[legs]
            if value is None or not math.isfinite(value):
                continue
            width = min(end, row["mean_start"] + FPS) - max(start, row["mean_start"])
            covered += width
            total += width * value
        return total / covered if covered >= INRUSH_COVERAGE * (end - start) else None

    def sizes(self, steps: list[Step]) -> dict[int, float]:
        out = {}
        for s, last in startup_candidates(steps):
            pre = self.mean(s.frame - 12 * FPS, s.frame - 2 * FPS, s.legs)
            settled = self.mean(last + 6 * FPS, last + 20 * FPS, s.legs)
            if pre is not None and settled is not None:
                rise = settled - pre
                if MIN_STEP_W <= rise < INRUSH_RATIO * s.w:
                    out[id(s)] = rise
        return out


BUCKET_W = 5.0
POWER_BIN_FRAMES = 5 * FPS


def bin_readings(readings: list[dict]) -> list[dict]:
    """Covered five-second maxima (pairing) and means (cycle energy).
    Missing/nonfinite values invalidate that leg. Same rule as load_readings' SQL.
    """
    bins: dict[int, list[dict]] = {}
    for row in readings:
        if row["frame"] >= 0:   # frame counters are never negative; skipped in the SQL path too
            bins.setdefault(row["frame"] // POWER_BIN_FRAMES, []).append(row)
    out = []
    for b, rows in sorted(bins.items()):
        rows.sort(key=lambda row: row["frame"])
        start = b * POWER_BIN_FRAMES
        if (rows[0]["frame"] > start + FPS or
                rows[-1]["frame"] < start + POWER_BIN_FRAMES - FPS or
                any(y["frame"] - x["frame"] > 2 * FPS for x, y in zip(rows, rows[1:]))):
            continue
        item = {"generation": rows[0].get("generation", 0), "bin_start": start}
        for leg, keys in (("L1", ("l1_w",)), ("L2", ("l2_w",)), ("both", ("l1_w", "l2_w"))):
            values = [sum(row[k] for k in keys) if all(
                row.get(k) is not None and math.isfinite(row[k]) for k in keys
            ) else math.inf for row in rows]
            item[leg] = max(values)
            item[leg + "_mean"] = sum(values) / len(values)
        out.append(item)
    return out


class PowerEvidence:
    """Range minima of sustained leg power, keyed by frame within one generation.

    A bin contributes its *maximum* power: all its samples must contradict an on
    before we reject it. Require coverage across the five-second bin, with no
    gaps over two seconds. Missing data and isolated dips are not evidence.
    Covered pre-start means anchor the floor to the background load; otherwise
    retain the absolute on-size floor. Combined loads use the earlier baseline.
    """

    def __init__(self, readings: list[dict], startup: StartupPower | None = None):
        # pre-binned rows (load_readings, done in SQL) or raw per-second readings (tests, replays)
        bins = readings if readings and "bin_start" in readings[0] else bin_readings(readings)
        self.startup = startup
        self.baselines = {}
        self.closed_frames = []
        self.closures = []
        self.frames = [b["bin_start"] for b in bins]
        powers = {leg: [b[leg] for b in bins] for leg in ("L1", "L2", "both")}
        # Segment trees keep each candidate check logarithmic rather than
        # rescanning hours of per-second readings for every unmatched on.
        self.n = len(self.frames)
        self.trees = {}
        for leg, values in powers.items():
            tree = [math.inf] * self.n + values
            for i in range(self.n - 1, 0, -1):
                tree[i] = min(tree[2 * i], tree[2 * i + 1])
            self.trees[leg] = tree

    def confirmed_off(self, run: Run, off: Step) -> None:
        """Only independently paired offs can remove a known baseline component.

        Preserve the observed conductor split; a both-leg total cannot safely be
        subtracted from a single leg. Shared-off hypotheses do not establish it.
        """
        values = {run.legs: run.w}
        if run.legs == "both" and off.leg_w:
            values.update({leg: run.w * abs(w / off.w) for leg, w in off.leg_w.items()})
        elif run.legs != "both":
            values["both"] = run.w
        # keep both lists ordered by off frame: contradicts() binary-searches closed_frames
        k = bisect_right(self.closed_frames, off.frame)
        self.closed_frames.insert(k, off.frame)
        self.closures.insert(k, (run.start_frame + run.stages[-1][0], values))

    def _below(self, legs: str, start: int, stop: int, floor: float) -> bool:
        lo = bisect_left(self.frames, start) + self.n
        hi = bisect_right(self.frames, stop) + self.n
        tree = self.trees[legs]
        while lo < hi:
            if lo % 2:
                if tree[lo] < floor:
                    return True
                lo += 1
            if hi % 2:
                hi -= 1
                if tree[hi] < floor:
                    return True
            lo //= 2
            hi //= 2
        return False

    def contradicts(self, run: Run, end_frame: int, *, baseline_run: Run | None = None,
                    combined_w: float | None = None) -> bool:
        # Leave startup/stages and the candidate off's own transition out.
        start = run.start_frame + run.stages[-1][0] + STAGE_FRAMES
        stop = end_frame - STAGE_FRAMES - POWER_BIN_FRAMES
        anchor = baseline_run if baseline_run is not None else run
        key = (anchor.start_frame, anchor.legs)
        if key not in self.baselines:
            self.baselines[key] = self.startup.mean(
                anchor.start_frame - 12 * FPS, anchor.start_frame - 2 * FPS, anchor.legs
            ) if self.startup is not None else None
        pre = self.baselines[key]
        w = run.w if combined_w is None else combined_w
        floor = (pre + .85 * w - min(SLACK_W, .30 * w) if pre is not None else
                 w - max(PAIR_TOL * w, PAIR_ABS_W))
        if pre is None:
            return self._below(run.legs, start, stop, floor)
        # A baseline may include an older appliance which has since closed.
        # Split the evidence interval at confirmed offs: compensation must never
        # conceal an earlier return to baseline or a load that started later.
        a = bisect_left(self.closed_frames, anchor.start_frame - 2 * FPS)
        b = bisect_right(self.closed_frames, stop)
        removed = 0.0
        for i in range(a, b):
            last_stage, values = self.closures[i]
            drop = values.get(run.legs, 0)
            if not drop or last_stage > anchor.start_frame - 12 * FPS:
                continue
            end = self.closed_frames[i]
            if self._below(run.legs, start, end - POWER_BIN_FRAMES, floor - min(pre, removed)):
                return True
            # Exclude a bin straddling the observed off, as for candidate offs.
            start = max(start, end)
            removed += drop
        return self._below(run.legs, start, stop, floor - min(pre, removed))


def pair_runs(steps: list[Step], now_ts: float, readings: list[dict] | None = None,
              keep_expired: bool = False, *, startup_readings: list[dict] | None = None) -> list[Run]:
    """Close each off with one compatible on, falling back to a bounded pair on the same legs.
    Open runs are indexed by legs and 5 W size bucket, so an off only looks at sizes it could match:
    thousands of unmatched ons (missed offs, noise) stay linear instead of quadratic.
    When readings are available, a sustained power floor below an on's expected level
    retires it as unresolved before it can steal a later off. No end is invented.
    """
    runs: list[Run] = []
    # Five-second maxima/means remain dedicated to pairing/cycles. Never use
    # a coarse bin straddling the on-step as its pre-start baseline.
    means = startup_readings if startup_readings is not None else readings or []
    startup = StartupPower(means) if means and "bin_start" not in means[0] else None
    sizes = startup.sizes(steps) if startup is not None else {}
    power = PowerEvidence(readings, startup) if readings else None
    open_by: dict[str, dict[int, list[tuple[int, Run]]]] = {}
    # Linked order keeps a capped reverse traversal O(cap), even after thousands
    # of newer starts close and leave only old unmatched ons behind.
    recent: dict[str, OrderedDict[int, Run]] = {}
    for s in steps:
        if s.w > 0:
            w = sizes.get(id(s), s.w)
            r = Run(s.frame, s.ts, s.legs, w, s.stages or [(0, round(s.w, 1))], status="running",
                    inrush_w=s.w if id(s) in sizes else None)
            open_by.setdefault(s.legs, {}).setdefault(int(w // BUCKET_W), []).append((len(runs), r))
            recent.setdefault(s.legs, OrderedDict())[len(runs)] = r
            runs.append(r)
            continue
        x = abs(s.w)
        # every on size that _compatible() could accept for this off
        tol = max(PAIR_TOL, INRUSH_PAIR_TOL)
        lo, hi = min(x / (1 + tol), x - PAIR_ABS_W), max(x / (1 - tol), x + PAIR_ABS_W)
        buckets = open_by.get(s.legs, {})
        opened = recent.get(s.legs, {})
        best = None
        for b in range(int(max(lo, 0) // BUCKET_W), int(hi // BUCKET_W) + 1):
            lst = buckets.get(b)
            if not lst:
                continue
            # ons older than MAX_RUN_S can't be closed any more (their off was missed): drop them
            for i, r in lst:
                if s.ts - r.start_ts > MAX_RUN_S:
                    opened.pop(i, None)
            lst[:] = [(i, r) for i, r in lst if s.ts - r.start_ts <= MAX_RUN_S]
            for i, r in reversed(lst):
                if _compatible(r, s):
                    if power is not None and power.contradicts(r, s.frame):
                        r.status = "unresolved"
                        opened.pop(i, None)
                        continue
                    if best is None or i > best[0]:
                        best = (i, r, b)
                    break  # lists are in start order: the first compatible from the end is this bucket's newest
            lst[:] = [(i, r) for i, r in lst if r.running]
        if best:
            i, r, b = best
            r.end_frame, r.end_ts, r.off_w, r.status = s.frame, s.ts, round(s.w, 1), "closed"
            buckets[b].remove((i, r))
            opened.pop(i, None)
            if power is not None:
                power.confirmed_off(r, s)
                # A completed newer cycle can disprove an older similar on:
                # during their supposed overlap the leg must support BOTH loads.
                # Retire the stale candidate, never manufacture an off for it.
                for b in range(int(max(lo, 0) // BUCKET_W), int(hi // BUCKET_W) + 1):
                    lst = buckets.get(b, [])
                    for j, older in lst:
                        if older.start_frame < r.start_frame and _compatible(older, s):
                            if power.contradicts(r, s.frame, combined_w=r.w + older.w,
                                                 baseline_run=older):
                                older.status = "unresolved"
                                opened.pop(j, None)
                    lst[:] = [(j, older) for j, older in lst if older.running]
        else:
            candidates = []
            for i, r in islice(reversed(opened.items()), SHARED_OFF_CAP):
                if s.ts - r.start_ts > MAX_RUN_S:
                    break
                candidates.append((i, r))
            pair, rank = None, None
            for a, (i, newer) in enumerate(candidates):
                for j, older in candidates[a + 1:]:
                    if newer.start_frame - older.start_frame > SHARED_START_S * FPS:
                        continue
                    total = newer.w + older.w
                    error = abs(total - x)
                    if error > max(PAIR_TOL * total, PAIR_ABS_W):
                        continue
                    if power is not None and (
                        power.contradicts(older, s.frame) or power.contradicts(newer, s.frame) or
                        # The overlap must support their summed load.
                        # The newer baseline already contains the older load.
                        power.contradicts(newer, s.frame, combined_w=total, baseline_run=older)
                    ):
                        continue
                    score = (abs(newer.start_frame - older.start_frame), error)
                    if rank is None or score < rank:
                        pair, rank = ((i, newer), (j, older)), score
            if pair is not None:
                for i, r in pair:
                    r.end_frame, r.end_ts, r.off_w, r.status = s.frame, s.ts, round(s.w, 1), "closed"
                    r.closed_by = "shared-off"
                    buckets[int(r.w // BUCKET_W)].remove((i, r))
                    opened.pop(i)
    # Pairing still expires at six hours. The cycle pipeline retains old unresolved
    # starts for 48-hour activity/membership; standalone run detection drops them.
    if keep_expired:
        for r in runs:
            if r.end_frame is None and now_ts - r.start_ts > MAX_RUN_S:
                r.status = "unresolved"
    else:
        runs = [r for r in runs if r.end_frame is not None or now_ts - r.start_ts <= MAX_RUN_S]
    if power is not None and readings:
        end = max(row["frame"] if "frame" in row else row["bin_start"] + POWER_BIN_FRAMES for row in readings) + STAGE_FRAMES
        for r in runs:
            if r.running and power.contradicts(r, end):
                r.status = "unresolved"
    return runs


def resolve_closed_runs(runs: list[Run], now_ts: float, named: set[int]) -> None:
    """Discard extreme duration mis-pairs using only their own pattern's closed peers.

    Require at least eight closed examples including the candidate, and exceed BOTH
    20x the peer median and 4x the peer nearest-rank 95th percentile. Leaving the
    candidate out prevents it from inflating its own tail threshold in small samples.
    Named patterns are exempt. Repeat until stable: rejected pairs no longer pollute
    the next pass, and every pass either removes a closure or terminates.
    """
    while True:
        by_pattern: dict[int, list[Run]] = {}
        for r in runs:
            if r.status == "closed" and r.pattern is not None and r.pattern not in named:
                by_pattern.setdefault(r.pattern, []).append(r)
        rejected = []
        for rs in by_pattern.values():
            if len(rs) < 8:
                continue
            rs.sort(key=lambda r: r.duration_s(now_ts))
            ds = [r.duration_s(now_ts) for r in rs]
            peers = len(ds) - 1
            for i, r in enumerate(rs):
                # Index into the sorted peers without copying the list for every run.
                mid, tail = peers // 2, math.ceil(.95 * peers) - 1
                median = ds[mid + (mid >= i)]
                p95 = ds[tail + (tail >= i)]
                if ds[i] > max(20 * median, 4 * p95):
                    rejected.append(r)
        if not rejected:
            return
        for r in rejected:
            r.end_frame = r.end_ts = r.off_w = None
            r.closed_by = None
            r.status = "unresolved"


def resolve_open_runs(runs: list[Run], now_ts: float, min_s: float = 600, factor: float = 3) -> None:
    """An unclosed run stays "running" only while younger than factor x its pattern's median closed
    duration (min_s when the pattern has never been seen closing); after that it's "unresolved"
    and adds no energy."""
    closed: dict[int, list[float]] = {}
    for r in runs:
        if r.status == "closed":
            closed.setdefault(r.pattern, []).append(r.duration_s(now_ts))
    for r in runs:
        if r.status in ("closed", "unresolved"):
            continue
        ds = sorted(closed.get(r.pattern, []))
        # with closed examples: 3x their median (a 13 s pulse isn't "on" after a minute); without: min_s
        limit = max(30.0, factor * ds[len(ds) // 2]) if ds else min_s
        r.status = "running" if now_ts - r.start_ts <= limit else "unresolved"


class Patterns:
    """Persistent pattern centres and user names, in settings.db."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        # assign() (detector thread) and find_or_create() (Device Hunt, event loop) both read-then-insert:
        # serialized, so they can't each create the same pattern
        self._write = threading.Lock()
        with self._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, legs TEXT NOT NULL, stages INTEGER NOT NULL,
                center_w REAL NOT NULL, n INTEGER NOT NULL DEFAULT 0, name TEXT, ignored INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, named_at REAL, last_seen REAL)""")
            cols = {r[1] for r in db.execute("PRAGMA table_info(patterns)")}
            if "last_seen" not in cols:
                db.execute("ALTER TABLE patterns ADD COLUMN last_seen REAL")
            if "inrush_w" not in cols:
                db.execute("ALTER TABLE patterns ADD COLUMN inrush_w REAL")
            db.execute("""CREATE TABLE IF NOT EXISTS pattern_runs (
                generation INTEGER NOT NULL, start_frame INTEGER NOT NULL, legs TEXT NOT NULL,
                start_ts REAL NOT NULL, pattern INTEGER NOT NULL, w REAL NOT NULL,
                PRIMARY KEY (generation, start_frame, legs))""")
            db.execute("""CREATE TABLE IF NOT EXISTS pattern_surges (
                pattern INTEGER PRIMARY KEY, w REAL NOT NULL)""")
            db.execute("INSERT OR IGNORE INTO pattern_surges SELECT id, inrush_w FROM patterns WHERE inrush_w IS NOT NULL")

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self) -> list[dict]:
        with self._conn() as db:
            return [dict(r) for r in db.execute("SELECT * FROM patterns")]

    def _snapshot(self, db) -> list[dict]:
        return [dict(r) for r in db.execute("SELECT * FROM patterns")]

    @staticmethod
    def _nearest(pats, legs, stages, w):
        eligible = [p for p in pats if p["legs"] == legs and p["stages"] == stages
                    and abs(w - p["center_w"]) <= CLUSTER_TOL * p["center_w"]]
        return min(eligible, key=lambda p: abs(w - p["center_w"]) / p["center_w"], default=None)

    def _surge_identities(self, db, pats, runs, persist):
        """Recover pre-resize membership, then migrate centres before clustering.

        Old settings contain no run membership. Reconstruct those associations
        from the historical reported shape against the *unmodified* snapshot,
        never from the new settled watts. Subsequent recomputes/restarts use the
        saved run key and size, even if a different named centre is now nearer.
        """
        saved = {(r["generation"], r["start_frame"], r["legs"]): r
                 for r in db.execute("SELECT * FROM pattern_runs")}
        surges = {r["pattern"]: r["w"] for r in db.execute("SELECT * FROM pattern_surges")}
        by_id = {p["id"]: p for p in pats}
        identities, migrated, members = {}, set(), {}
        for r in runs:
            if r.inrush_w is None:
                continue
            key = (r.generation, r.start_frame, r.legs)
            old = saved.get(key)
            if old is not None and old["start_ts"] == r.start_ts and old["pattern"] in by_id:
                p, old_w = by_id[old["pattern"]], old["w"]
            else:
                p = self._nearest(pats, r.legs, min(len(r.stages), 2), r.inrush_w)
                old_w = r.inrush_w
                if p is None:
                    # A new activation of an already migrated appliance can use
                    # its recorded surge signature as well as its settled size.
                    p = self._nearest([p for p in pats if p["id"] in surges
                                       and abs(r.inrush_w - surges[p["id"]]) <= CLUSTER_TOL * surges[p["id"]]],
                                      r.legs, min(len(r.stages), 2), r.w)
                    old_w = r.w
            if p is None:
                continue
            identities[key] = p
            members.setdefault(p["id"], []).append(r.w)
            if r.w < INRUSH_RATIO * old_w:
                migrated.add(p["id"])
        if persist:
            for pid in migrated:
                p = by_id[pid]
                p["center_w"] = sorted(members[pid])[len(members[pid]) // 2]
                db.execute("UPDATE patterns SET center_w=? WHERE id=?", (p["center_w"], pid))
        return identities

    def assign(self, runs: list[Run], *, persist: bool = True) -> None:
        """Assign shapes; cycle-only evidence uses temporary IDs without changing run patterns."""
        with self._write, self._conn() as db:
            pats = self._snapshot(db)
            identities = self._surge_identities(db, pats, runs, persist)
            for r in runs:
                stages = min(len(r.stages), 2)  # 1 = single step, 2 = multi-stage
                best = identities.get((r.generation, r.start_frame, r.legs))
                if best is None:
                    # An unassociated surge must not acquire another appliance's
                    # name/ignore state just because its new wattage is nearby.
                    eligible = [p for p in pats if r.inrush_w is None or not (
                        p.get("name") or p.get("ignored"))]
                    best = self._nearest(eligible, r.legs, stages, r.w)
                if best is None:
                    if persist:
                        cur = db.execute("INSERT INTO patterns (legs, stages, center_w, n, created) VALUES (?,?,?,?,?)",
                                         (r.legs, stages, r.w, 0, time.time()))
                        pid = cur.lastrowid
                    else:
                        pid = -len(pats) - 1
                    best = {"id": pid, "legs": r.legs, "stages": stages, "center_w": r.w, "n": 0, "name": None}
                    pats.append(best)
                r.pattern = best["id"]
            if not persist:
                return
            db.executemany("INSERT OR REPLACE INTO pattern_runs VALUES (?,?,?,?,?,?)",
                           [(r.generation, r.start_frame, r.legs, r.start_ts, r.pattern, r.w) for r in runs])
            if runs:
                db.execute("DELETE FROM pattern_runs WHERE start_ts < ?",
                           (max(r.start_ts for r in runs) - WINDOW_S,))
            # refresh centres (unnamed only) and counts from this window's runs
            by_id: dict[int, list[Run]] = {}
            for r in runs:
                by_id.setdefault(r.pattern, []).append(r)
            for p in pats:
                rs = by_id.get(p["id"], [])
                if not rs:
                    db.execute("UPDATE patterns SET n=0, inrush_w=NULL WHERE id=?", (p["id"],))
                    continue
                if not p.get("name"):
                    p["center_w"] = sorted(x.w for x in rs)[len(rs) // 2]  # median
                # the name guard is in SQL: a name set by the API after our snapshot still freezes the centre
                surges = [x.inrush_w for x in rs if x.inrush_w is not None]
                if surges:
                    # Keep the identifying signature after the public window's
                    # inrush median becomes null and run associations expire.
                    db.execute("INSERT OR REPLACE INTO pattern_surges VALUES (?,?)",
                               (p["id"], median(surges)))
                db.execute("UPDATE patterns SET center_w = CASE WHEN name IS NULL THEN ? ELSE center_w END, n=?, last_seen=?, inrush_w=? WHERE id=?",
                           (p["center_w"], len(rs), max(x.start_ts for x in rs), median(surges) if surges else None, p["id"]))
            # one-off sizes (noise, rare coincidences) would otherwise pile up forever
            db.execute("DELETE FROM patterns WHERE name IS NULL AND ignored=0 AND n=0 AND coalesce(last_seen, created) < ?",
                       (time.time() - PRUNE_AFTER_S,))
            db.execute("DELETE FROM pattern_surges WHERE pattern NOT IN (SELECT id FROM patterns)")

    def name(self, pattern_id: int, name: str | None) -> dict:
        name = (name or "").strip()[:60] or None
        with self._conn() as db:
            cur = db.execute("UPDATE patterns SET name=?, ignored=0, named_at=? WHERE id=?",
                             (name, time.time() if name else None, pattern_id))
            if not cur.rowcount:
                raise KeyError(pattern_id)
            return dict(db.execute("SELECT * FROM patterns WHERE id=?", (pattern_id,)).fetchone())

    def ignore(self, pattern_id: int, ignored: bool = True) -> dict:
        with self._conn() as db:
            cur = db.execute("UPDATE patterns SET ignored=? WHERE id=?", (int(ignored), pattern_id))
            if not cur.rowcount:
                raise KeyError(pattern_id)
            return dict(db.execute("SELECT * FROM patterns WHERE id=?", (pattern_id,)).fetchone())

    def find_or_create(self, legs: str, stages: int, w: float) -> dict:
        """The pattern a run of this shape would join (nearest centre within CLUSTER_TOL), created if
        none exists yet. Device Hunt names it, so runs of a device never seen before still get the name."""
        if legs not in ("L1", "L2", "both") or w < MIN_STEP_W:
            raise ValueError("legs must be L1, L2 or both and w at least MIN_STEP_W")
        stages = 2 if stages > 1 else 1
        with self._write, self._conn() as db:
            best, best_err = None, None
            for p in self._snapshot(db):
                if p["legs"] != legs or p["stages"] != stages:
                    continue
                err = abs(w - p["center_w"]) / p["center_w"]
                if err <= CLUSTER_TOL and (best_err is None or err < best_err):
                    best, best_err = p, err
            if best is not None:
                return best
            now = time.time()
            cur = db.execute("INSERT INTO patterns (legs, stages, center_w, n, created, last_seen) VALUES (?,?,?,?,?,?)",
                             (legs, stages, round(w, 1), 0, now, now))
            return dict(db.execute("SELECT * FROM patterns WHERE id=?", (cur.lastrowid,)).fetchone())


def load_deltas(db_path: Path, since_ts: float) -> list[dict]:
    """Deltas in the window, all frame-counter generations (uses the ts index, not a scan)."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(
            # no ORDER BY: it would make SQLite walk the primary key instead of the ts index; to_steps sorts
            "SELECT generation, start_frame, channel, w, ts FROM deltas WHERE ts >= ?",
            (since_ts,))]
    finally:
        db.close()


def detect_runs(deltas: list[dict], now_ts: float, readings: list[dict] | None = None,
                keep_expired: bool = False, *, startup_readings: list[dict] | None = None) -> list[Run]:
    """Steps and runs per frame-counter generation (frames only compare within one), combined.
    A run spanning a monitor reboot can't close. keep_expired retains missed offs
    as unresolved so the cycle pipeline can use the entire 48-hour window.
    """
    by_gen: dict[int, list[dict]] = {}
    for d in deltas:
        by_gen.setdefault(d.get("generation", 0), []).append(d)
    readings_by_gen: dict[int, list[dict]] = {}
    for row in readings or []:
        readings_by_gen.setdefault(row.get("generation", 0), []).append(row)
    startup_by_gen: dict[int, list[dict]] = {}
    for row in startup_readings or []:
        startup_by_gen.setdefault(row.get("generation", 0), []).append(row)
    runs: list[Run] = []
    for gen in sorted(by_gen):
        means = startup_by_gen.get(gen, []) if startup_readings is not None else None
        for r in pair_runs(to_steps(by_gen[gen]), now_ts, readings_by_gen.get(gen), keep_expired,
                           startup_readings=means):
            r.generation = gen
            runs.append(r)
    return runs


def load_startup_readings(db_path: Path, deltas: list[dict]) -> list[dict]:
    """SQL baseline means for every on, plus eligible starts' settled means.

    Avoid materializing 48 hours of fine bins or changing five-second cycle energy.
    Coalesce overlapping windows so each reading is loaded at most once.
    """
    by_gen: dict[int, list[dict]] = {}
    for d in deltas:
        by_gen.setdefault(d.get("generation", 0), []).append(d)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = []
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='readings'").fetchone():
            return []
        for gen, ds in by_gen.items():
            windows = []
            steps = to_steps(ds)
            windows.extend((max(0, (s.frame - 12 * FPS) // FPS * FPS),
                            math.ceil((s.frame - 2 * FPS) / FPS) * FPS)
                           for s in steps if s.w > 0)
            for s, last in startup_candidates(steps):
                start, stop = last + 6 * FPS, last + 20 * FPS
                windows.append((start // FPS * FPS, math.ceil(stop / FPS) * FPS))
            merged = []
            for start, stop in sorted(windows):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(stop, merged[-1][1])
                else:
                    merged.append([start, stop])
            for start, stop in merged:
                rows = db.execute("""
                    SELECT frame / :fps, AVG(l1_w), AVG(l2_w), AVG(l1_w + l2_w),
                           SUM(l1_w IS NULL), SUM(l2_w IS NULL)
                    FROM readings WHERE generation=:gen AND frame>=:start AND frame<:stop
                    GROUP BY frame / :fps ORDER BY frame / :fps""",
                    {"fps": FPS, "gen": gen, "start": start, "stop": stop})
                for k, a, b, both, na, nb in rows:
                    out.append({"generation": gen, "mean_start": k * FPS,
                                "L1": math.inf if na else a, "L2": math.inf if nb else b,
                                "both": math.inf if na or nb else both})
        return out
    finally:
        db.close()


def load_readings(db_path: Path, since_ts: float) -> list[dict]:
    """Power evidence for the same window as deltas; older delta-only DBs still work."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='readings'").fetchone():
            return []
        # binned in SQL (48 h of per-second readings is ~350k rows; the bins are ~35k), with the same
        # coverage rule as bin_readings: first/last within a second of the bin edges, no gap over 2 s
        rows = db.execute("""
            WITH r AS (
                SELECT generation AS g, frame AS f, l1_w AS a, l2_w AS b, frame / :bin AS k,
                       frame - LAG(frame) OVER (PARTITION BY generation, frame / :bin ORDER BY frame) AS gap
                FROM readings WHERE ts >= :since AND frame >= 0)   -- SQLite / truncates, Python // floors
            SELECT g, k, MIN(f), MAX(f), COALESCE(MAX(gap), 0), MAX(a), MAX(b), MAX(a + b),
                   SUM(a IS NULL), SUM(b IS NULL), AVG(a), AVG(b), AVG(a + b)
            FROM r GROUP BY g, k ORDER BY g, k""", {"bin": POWER_BIN_FRAMES, "since": since_ts}).fetchall()
        out = []
        for g, k, first, last, gap, a, b, both, na, nb, ma, mb, mab in rows:
            start = k * POWER_BIN_FRAMES
            if first > start + FPS or last < start + POWER_BIN_FRAMES - FPS or gap > 2 * FPS:
                continue
            out.append({"generation": g, "bin_start": start, "L1": math.inf if na else a, "L2": math.inf if nb else b,
                        "both": math.inf if na or nb else both,
                        "L1_mean": math.inf if na else ma, "L2_mean": math.inf if nb else mb,
                        "both_mean": math.inf if na or nb else mab})
        return out
    finally:
        db.close()


def load_sense_ons(db_path: Path, since_ts: float) -> list[dict]:
    """Moments Sense's own detection says a device turned on (from device_power), with its size:
    the device's peak watts in the 30 s after (Sense ramps its estimate up over a few updates)."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT ts, device_id, name, w FROM device_power WHERE ts >= ? AND name NOT IN ('Always On', 'Other') "
                          "ORDER BY device_id, ts", (since_ts,)).fetchall()
    finally:
        db.close()
    ON_W = 20.0
    ons, prev = [], {}
    for i, (ts, dev, name, w) in enumerate(rows):
        w = w or 0.0
        p = prev.get(dev, 0.0)
        # a crossing of ON_W from below is one turn-on; Sense ramping its estimate (203 -> 298 -> 1700)
        # or creeping up (0 -> 15 -> 100) still counts once
        if w >= ON_W and p < ON_W:
            peak = w
            j = i + 1
            while j < len(rows):  # the whole 30 s, however many rows that is
                r = rows[j]
                if r[1] != dev or r[0] > ts + 30 or (r[3] or 0) < ON_W:
                    break  # stop at the next device, after 30 s, or when it turns off (no next activation)
                peak = max(peak, r[3] or 0)
                j += 1
            ons.append({"ts": ts, "name": name, "w": peak})
        prev[dev] = w
    return ons


def sense_hints(runs: list[Run], ons: list[dict], min_share: float = 0.4, min_matches: int = 2) -> dict[int, dict]:
    """Per pattern: the Sense device whose turn-ons line up with this pattern's runs (start within
    -10..+30 s, Sense lags; size within 40%). Weak labels: shown as a suggestion, never applied."""
    from bisect import bisect_left
    # global nearest assignment: each Sense turn-on goes to the closest compatible run start (not to
    # whichever run happened to be processed first), and each run counts a device name at most once
    starts = sorted(range(len(runs)), key=lambda k: runs[k].start_ts)
    start_ts = [runs[k].start_ts for k in starts]
    totals: dict[int, int] = {}
    for r in runs:
        totals[r.pattern] = totals.get(r.pattern, 0) + 1
    voted: set[tuple[int, str]] = set()
    votes: dict[int, dict[str, int]] = {}
    for o in ons:
        # candidate runs start within 30 s before to 10 s after the onset (Sense lags the real step)
        j = bisect_left(start_ts, o["ts"] - 30)
        best = None
        while j < len(starts) and start_ts[j] <= o["ts"] + 10:
            r = runs[starts[j]]
            if abs(o["w"] - r.w) <= 0.4 * max(r.w, o["w"]):
                gap = abs(o["ts"] - r.start_ts)
                if best is None or gap < best[0]:
                    best = (gap, starts[j])
            j += 1
        if best and (best[1], o["name"]) not in voted:
            voted.add((best[1], o["name"]))
            pid = runs[best[1]].pattern
            votes.setdefault(pid, {})[o["name"]] = votes.setdefault(pid, {}).get(o["name"], 0) + 1
    hints = {}
    for pid, v in votes.items():
        name, n = max(v.items(), key=lambda kv: kv[1])
        share = n / totals[pid]
        if n >= min_matches and share >= min_share:
            hints[pid] = {"name": name, "matches": n, "share": round(share, 2)}
    return hints


class Detector:
    """Recomputes runs over the window and keeps the latest result for the API."""

    def __init__(self, deltas_db: Path, settings_db: Path):
        from .cycles import CyclePatterns

        self.deltas_db = deltas_db
        self.patterns = Patterns(settings_db)
        self.cycle_patterns = CyclePatterns(settings_db)
        self.cycles = []
        self.runs: list[Run] = []
        self.hints: dict[int, dict] = {}
        self.computed_at = 0.0

    def recompute(self, now_ts: float | None = None) -> None:
        from .cycles import BASELINE_S, detect_cycles, run_key

        now_ts = now_ts if now_ts is not None else time.time()
        since = now_ts - WINDOW_S
        deltas = load_deltas(self.deltas_db, since)
        readings = load_readings(self.deltas_db, since - BASELINE_S)
        startup = load_startup_readings(self.deltas_db, deltas)
        runs = detect_runs(deltas, now_ts, readings, startup_readings=startup)
        self.patterns.assign(runs)
        patterns = self.patterns.all()
        named = {p["id"] for p in patterns if p["name"]}
        resolve_closed_runs(runs, now_ts, named)
        resolve_open_runs(runs, now_ts)
        # Retain yesterday's missed offs only as cycle evidence. Temporary pattern
        # assignment must not create/count/prune patterns or change public run status.
        cycle_runs = detect_runs(deltas, now_ts, readings, keep_expired=True, startup_readings=startup)
        self.patterns.assign(cycle_runs, persist=False)
        resolve_closed_runs(cycle_runs, now_ts, named)
        resolve_open_runs(cycle_runs, now_ts)
        temporary = {}
        for r in cycle_runs:
            if r.pattern < 0:
                temporary.setdefault(r.pattern, []).append(r)
        cycle_shapes = {pid: dict(id=pid, legs=rs[0].legs, name=None,
                                 center_w=sorted(r.w for r in rs)[len(rs) // 2])
                        for pid, rs in temporary.items()}
        cycles = detect_cycles(deltas, cycle_runs, patterns + list(cycle_shapes.values()), readings, now_ts)
        membership = {run_key(r): r.cycle for r in cycle_runs}
        for r in runs:
            r.cycle = membership.get(run_key(r))
        self.cycle_patterns.assign(cycles, now_ts)
        try:
            self.hints = sense_hints(runs, load_sense_ons(self.deltas_db, now_ts - WINDOW_S))
        except sqlite3.Error:
            self.hints = {}
        self.runs, self.cycles, self.computed_at = runs, cycles, now_ts

    def cycles_between(self, start: float, end: float) -> list[dict]:
        names = {p["id"]: p["name"] for p in self.cycle_patterns.all()}
        return [c.as_dict(names.get(c.pattern)) for c in self.cycles
                if c.start_ts < end and c.end_ts > start]

    def cycle_loads(self, day_start: float) -> list[dict]:
        from .cycles import pattern_stats

        return pattern_stats(self.cycle_patterns.all(), self.cycles, day_start)

    def runs_since(self, since_ts: float) -> list[dict]:
        return self.runs_between(since_ts, time.time() + 1)

    def runs_between(self, start: float, end: float) -> list[dict]:
        """Runs overlapping [start, end); unresolved runs have no known end, so only their start counts."""
        now = time.time()
        out = []
        for r in self.runs:
            if r.start_ts >= end:
                continue
            if r.status == "unresolved":
                keep = r.start_ts >= start          # no known end: only its start can fall in the window
            else:
                stop = r.end_ts if r.end_ts is not None else now
                keep = stop > start                 # strict: a run ending exactly at the window start is outside
            if keep:
                out.append(r.as_dict(now))
        return out

    def loads(self, day_start: float) -> list[dict]:
        """Every pattern seen in the window with today's stats, most energy first."""
        now = time.time()
        stats: dict[int, dict] = {}
        for r in self.runs:
            s = stats.setdefault(r.pattern, {"runs_window": 0, "runs_today": 0, "wh_today": 0.0, "wh_window": 0.0, "durations": [],
                                             "last_ts": 0.0, "running": False, "example": None, "cycle_runs": 0})
            s["runs_window"] += 1
            s["cycle_runs"] += r.cycle is not None
            s["wh_window"] += r.w * r.duration_s(now) / 3600
            if r.status == "closed":
                s["durations"].append(r.duration_s(now))
            s["last_ts"] = max(s["last_ts"], r.start_ts)
            s["running"] = s["running"] or r.running
            if s["example"] is None or len(r.stages) > len(s["example"]["stages"]):
                s["example"] = r.as_dict(now)
            if r.start_ts >= day_start:
                s["runs_today"] += 1
            # energy today: the part of the run after midnight (a 23:30-00:30 load counts its last half hour)
            if r.status != "unresolved":
                end = r.end_ts if r.end_ts is not None else now
                overlap = max(0.0, end - max(r.start_ts, day_start))
                s["wh_today"] += r.w * overlap / 3600
        out = []
        for p in self.patterns.all():
            s = stats.get(p["id"])
            if not s:
                if not p["name"]:
                    continue
                # named patterns stay listed even when not seen in the window
                s = {"runs_window": 0, "runs_today": 0, "wh_today": 0.0, "wh_window": 0.0, "durations": [], "last_ts": p.get("last_seen") or 0.0,
                     "running": False, "example": None, "cycle_runs": 0}
            ds = sorted(s.pop("durations"))
            out.append({**p, **s, "sense_hint": self.hints.get(p["id"]), "wh_today": round(s["wh_today"], 1), "wh_window": round(s["wh_window"], 1),
                        "median_duration_s": round(ds[len(ds) // 2], 1) if ds else None, "closed_runs": len(ds),
                        "part_of_cycles": bool(s["runs_window"] and s["cycle_runs"] / s["runs_window"] >= .7)})
        out.sort(key=lambda x: (-x["wh_today"], -x["runs_window"]))
        return out
