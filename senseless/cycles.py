"""Program activity inferred from steps and baseline-subtracted leg power.

These are activity envelopes, not identified appliances or measured off times.
All frame comparisons and power evidence stay inside one monitor generation.
"""

import math
import sqlite3
import threading
import time
from bisect import bisect_left
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from statistics import median

from .detect import FPS, POWER_BIN_FRAMES, Run, bin_readings, to_steps
from .hunt import CHATTER_MAX_S, CHATTER_MAX_W, CHATTER_MIN_RUNS

MIN_STEP_W = 120
GAP_S = 240  # the replayed wash has a 210 s pause; 180 s splits it
MIN_STEPS = 12
MIN_DURATION_S = 300
MAX_DURATION_S = 4 * 3600
MIN_STEP_RATE = .15
MIN_UNPAIRED_SHARE = .5  # clean on/off appliances are runs, not programs
BASELINE_S = 120
MIN_POWER_W = 100
MIN_COVERAGE = .8
PRUNE_AFTER_S = 14 * 86400
FEATURES = {"duration_s": .35, "energy_wh": .4, "median_step_w": .4}
CENTRES = (*FEATURES, "step_rate")


@dataclass
class Cycle:
    generation: int
    start_frame: int
    start_ts: float
    end_ts: float
    legs: str
    steps: int
    step_rate: float
    median_step_w: float
    p90_step_w: float
    energy_wh: float
    peak_w: float
    has_heat: bool
    member_run_keys: list = field(default_factory=list)
    pattern: int | None = None

    @property
    def id(self):
        return f"{self.generation}:{self.legs}:{self.start_frame}"

    @property
    def duration_s(self):
        return self.end_ts - self.start_ts

    def as_dict(self, name=None):
        return {**self.__dict__, "id": self.id, "duration_s": self.duration_s, "name": name}


def run_key(run: Run):
    return run.generation, run.start_frame, run.legs


def chatter_patterns(runs, patterns, now_ts):
    """Hunt's frequency/closed-median/size rule, using this recompute's runs."""
    grouped = defaultdict(list)
    for r in runs:
        grouped[r.pattern].append(r)
    out = set()
    for p in patterns:
        rs = grouped[p["id"]]
        ds = sorted(r.duration_s(now_ts) for r in rs if r.status == "closed")
        if p["legs"] in ("L1", "L2") and len(rs) >= CHATTER_MIN_RUNS and ds and \
                ds[len(ds) // 2] <= CHATTER_MAX_S and p["center_w"] <= CHATTER_MAX_W:
            out.add(p["id"])
    return out


class CyclePower:
    """Five-second means for energy, maxima for peak; never integrate outages."""

    def __init__(self, rows):
        self.bins = rows if rows and "bin_start" in rows[0] else bin_readings(rows)
        self.bins = sorted(self.bins, key=lambda r: r["bin_start"])
        self.frames = [r["bin_start"] for r in self.bins]

    def interval(self, start, end, legs):
        lo = bisect_left(self.frames, start - POWER_BIN_FRAMES + 1)
        hi = bisect_left(self.frames, end)
        for row in self.bins[lo:hi]:
            mean = row.get(legs + "_mean", math.inf)
            peak = row[legs]
            if not math.isfinite(mean) or not math.isfinite(peak):
                continue
            width = min(end, row["bin_start"] + POWER_BIN_FRAMES) - max(start, row["bin_start"])
            if width > 0:
                yield width / FPS, mean, peak

    def measure(self, start, end, legs):
        baseline = list(self.interval(start - BASELINE_S * FPS, start, legs))
        activity = list(self.interval(start, end, legs))
        before = sum(s for s, _, _ in baseline)
        during = sum(s for s, _, _ in activity)
        if before < MIN_COVERAGE * BASELINE_S or during < MIN_COVERAGE * (end - start) / FPS:
            return None
        base = sum(s * mean for s, mean, _ in baseline) / before
        energy_ws = sum(s * (mean - base) for s, mean, _ in activity)
        if energy_ws / during < MIN_POWER_W:
            return None
        return energy_ws / 3600, max(peak - base for _, _, peak in activity)


def detect_cycles(deltas, runs, patterns, readings, now_ts):
    """Rebuild envelopes and membership after run assignment/status resolution."""
    chatter = chatter_patterns(runs, patterns, now_ts)
    excluded = chatter | {p["id"] for p in patterns if p["name"]}
    lookup = {run_key(r): r for r in runs}
    by_gen, power_by_gen = defaultdict(list), defaultdict(list)
    for r in runs:
        r.cycle = None
    for d in deltas:
        by_gen[d.get("generation", 0)].append(d)
    for row in readings:
        power_by_gen[row.get("generation", 0)].append(row)
    cycles = []
    for gen, ds in sorted(by_gen.items()):
        steps = to_steps(ds)
        power = CyclePower(power_by_gen[gen])
        candidates = defaultdict(list)
        for s in steps:
            run = lookup.get((gen, s.frame, s.legs))
            if s.w >= MIN_STEP_W and run is not None and run.pattern not in excluded:
                candidates[s.legs].append((s, run))
        for legs, ss in candidates.items():
            bursts = []
            for s, r in ss:
                if not bursts or s.frame - bursts[-1][-1][0].frame > GAP_S * FPS:
                    bursts.append([])
                bursts[-1].append((s, r))
            for burst in bursts:
                first, last = burst[0][0], burst[-1][0]
                duration = (last.frame - first.frame) / FPS
                if len(burst) < MIN_STEPS or not MIN_DURATION_S <= duration <= MAX_DURATION_S:
                    continue
                rate = len(burst) * 60 / duration
                unpaired = sum(r.status != "closed" for _, r in burst) / len(burst)
                if rate < MIN_STEP_RATE or unpaired < MIN_UNPAIRED_SHARE:
                    continue
                measured = power.measure(first.frame, last.frame, legs)
                if measured is None:
                    continue
                sizes = sorted(s.w for s, _ in burst)
                cycle = Cycle(gen, first.frame, first.ts, last.ts, legs, len(burst), rate,
                              median(sizes), sizes[math.ceil(.9 * len(sizes)) - 1], *measured,
                              any(s.legs == legs and first.frame <= s.frame <= last.frame and s.w >= 1000
                                  for s in steps))
                for r in runs:
                    if r.generation == gen and r.legs == legs and first.ts <= r.start_ts <= last.ts:
                        r.cycle = cycle.id
                        cycle.member_run_keys.append(run_key(r))
                cycles.append(cycle)
    return sorted(cycles, key=lambda c: (c.start_ts, c.id))


class CyclePatterns:
    """Persistent names and frozen named centres in settings.db, never the collector DB."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._write = threading.Lock()
        with self._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cycle_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, legs TEXT NOT NULL,
                duration_s REAL NOT NULL, energy_wh REAL NOT NULL, step_rate REAL NOT NULL,
                median_step_w REAL NOT NULL, n INTEGER NOT NULL DEFAULT 0, name TEXT,
                ignored INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, named_at REAL, last_seen REAL)""")

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self):
        with self._conn() as db:
            return [dict(r) for r in db.execute("SELECT * FROM cycle_patterns ORDER BY id")]

    def assign(self, cycles, now_ts=None):
        now_ts = time.time() if now_ts is None else now_ts
        with self._write, self._conn() as db:
            pats = [dict(r) for r in db.execute("SELECT * FROM cycle_patterns ORDER BY id")]
            grouped = defaultdict(list)
            for c in cycles:
                matches = []
                for p in pats:
                    if p["legs"] != c.legs:
                        continue
                    errs = [abs(getattr(c, k) - p[k]) / p[k] / tol for k, tol in FEATURES.items()]
                    if max(errs) <= 1:
                        matches.append((sum(e * e for e in errs), p["id"], p))
                if matches:
                    best = min(matches, key=lambda item: item[:2])[2]
                else:
                    values = [getattr(c, k) for k in CENTRES]
                    cur = db.execute("""INSERT INTO cycle_patterns
                        (legs,duration_s,energy_wh,median_step_w,step_rate,created) VALUES (?,?,?,?,?,?)""",
                                     (c.legs, *values, now_ts))
                    best = dict(id=cur.lastrowid, legs=c.legs, name=None, **dict(zip(CENTRES, values)))
                    pats.append(best)
                c.pattern = best["id"]
                grouped[c.pattern].append(c)
            for p in pats:
                cs = grouped[p["id"]]
                if not cs:
                    db.execute("UPDATE cycle_patterns SET n=0 WHERE id=?", (p["id"],))
                    continue
                values = [median(getattr(c, k) for c in cs) for k in CENTRES]
                updates = ",".join(f"{k}=CASE WHEN name IS NULL THEN ? ELSE {k} END" for k in CENTRES)
                db.execute(f"UPDATE cycle_patterns SET {updates}, n=?,last_seen=? WHERE id=?",
                           (*values, len(cs), max(c.start_ts for c in cs), p["id"]))
            db.execute("""DELETE FROM cycle_patterns WHERE name IS NULL AND ignored=0 AND n=0
                AND coalesce(last_seen,created) < ?""", (now_ts - PRUNE_AFTER_S,))

    def name(self, pattern_id, name):
        name = (name or "").strip()[:60] or None
        with self._write, self._conn() as db:
            cur = db.execute("UPDATE cycle_patterns SET name=?,ignored=0,named_at=? WHERE id=?",
                             (name, time.time() if name else None, pattern_id))
            if not cur.rowcount:
                raise KeyError(pattern_id)
            return dict(db.execute("SELECT * FROM cycle_patterns WHERE id=?", (pattern_id,)).fetchone())

    def ignore(self, pattern_id, ignored=True):
        with self._write, self._conn() as db:
            cur = db.execute("UPDATE cycle_patterns SET ignored=? WHERE id=?", (int(ignored), pattern_id))
            if not cur.rowcount:
                raise KeyError(pattern_id)
            return dict(db.execute("SELECT * FROM cycle_patterns WHERE id=?", (pattern_id,)).fetchone())


def pattern_stats(patterns, cycles, day_start):
    grouped = defaultdict(list)
    for c in cycles:
        grouped[c.pattern].append(c)
    out = []
    for p in patterns:
        cs = grouped[p["id"]]
        # Named patterns stay visible, including zero-count Prometheus samples.
        if not cs and not p["name"]:
            continue
        today_wh = sum(c.energy_wh * max(0, c.end_ts - max(c.start_ts, day_start)) / c.duration_s for c in cs)
        out.append({**p, "cycles_window": len(cs), "cycles_today": sum(c.start_ts >= day_start for c in cs),
                    "kwh_window": sum(c.energy_wh for c in cs) / 1000, "kwh_today": today_wh / 1000,
                    "median_duration_s": median(c.duration_s for c in cs) if cs else None})
    return sorted(out, key=lambda p: (-p["kwh_today"], p["id"]))
