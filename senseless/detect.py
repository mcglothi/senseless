"""Load detection from the monitor's on-board steps (deltas).

Pipeline, recomputed over a sliding window (cheap: a few thousand deltas a day):

1. **Steps**: steps on L1 and L2 in the same AC cycle with the same sign and about the same
   size are one 240 V step (legs "both").
2. **Stages**: same-sign steps on the same legs within STAGE_FRAMES are one multi-stage step
   (a microwave: +299 W, then +1,726 W 90 cycles later).
3. **Runs**: an "on" step is closed by the most recent compatible "off" on the same legs (size
   within PAIR_TOL). Unclosed ons younger than MAX_RUN_S are runs still in progress.
4. **Patterns**: runs are grouped by legs + stage count + size; a run joins the nearest known
   pattern centre within CLUSTER_TOL, else starts a new one. Patterns and their names persist in
   settings.db, so names survive recomputes and restarts.
"""

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

MIN_STEP_W = 30          # below this the on-board steps are mostly noise floor
PAIR_240_TOL = 0.25      # L1/L2 size mismatch allowed for one 240 V step
STAGE_FRAMES = 120       # 2 s at 60 Hz (microwave stages: 90 cycles apart)
PAIR_TOL = 0.15          # off step within 15% (or PAIR_ABS_W) of the on step
PAIR_ABS_W = 20
MAX_RUN_S = 6 * 3600
CLUSTER_TOL = 0.12
WINDOW_S = 48 * 3600
PRUNE_AFTER_S = 7 * 86400   # unnamed patterns unseen this long are deleted
FPS = 60


@dataclass
class Step:
    frame: int
    ts: float
    legs: str            # "L1" | "L2" | "both"
    w: float             # signed total
    stages: list = field(default_factory=list)  # [(frame offset, w)] for multi-stage ons


@dataclass
class Run:
    start_frame: int
    start_ts: float
    legs: str
    w: float             # on size (positive)
    stages: list
    end_frame: int | None = None
    end_ts: float | None = None
    off_w: float | None = None
    pattern: int | None = None
    # closed: off seen. running: no off yet, still plausible for its pattern. unresolved: no off and
    # older than its pattern ever runs (the off was missed: Sense's on/off steps aren't always symmetric).
    status: str = "closed"
    generation: int = 0

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
                "stages": self.stages, "duration_s": round(d, 1), "running": self.running, "status": self.status,
                "wh": round(self.w * d / 3600, 2), "pattern": self.pattern}


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
            steps.append(Step(frame, a["ts"], "both", a["w"] + b["w"]))
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
    return diff <= max(PAIR_TOL * on.w, PAIR_ABS_W)


BUCKET_W = 5.0


def pair_runs(steps: list[Step], now_ts: float) -> list[Run]:
    """Close each off with the most recent compatible open on (same legs, size within tolerance).
    Open runs are indexed by legs and 5 W size bucket, so an off only looks at sizes it could match:
    thousands of unmatched ons (missed offs, noise) stay linear instead of quadratic."""
    runs: list[Run] = []
    open_by: dict[str, dict[int, list[tuple[int, Run]]]] = {}
    for s in steps:
        if s.w > 0:
            r = Run(s.frame, s.ts, s.legs, s.w, s.stages or [(0, round(s.w, 1))], status="running")
            open_by.setdefault(s.legs, {}).setdefault(int(s.w // BUCKET_W), []).append((len(runs), r))
            runs.append(r)
            continue
        x = abs(s.w)
        # every on size that _compatible() could accept for this off
        lo, hi = min(x / (1 + PAIR_TOL), x - PAIR_ABS_W), max(x / (1 - PAIR_TOL), x + PAIR_ABS_W)
        buckets = open_by.get(s.legs, {})
        best = None
        for b in range(int(max(lo, 0) // BUCKET_W), int(hi // BUCKET_W) + 1):
            lst = buckets.get(b)
            if not lst:
                continue
            # ons older than MAX_RUN_S can't be closed any more (their off was missed): drop them
            lst[:] = [(i, r) for i, r in lst if s.ts - r.start_ts <= MAX_RUN_S]
            for i, r in reversed(lst):
                if _compatible(r, s):
                    if best is None or i > best[0]:
                        best = (i, r, b)
                    break  # lists are in start order: the first compatible from the end is this bucket's newest
        if best:
            i, r, b = best
            r.end_frame, r.end_ts, r.off_w, r.status = s.frame, s.ts, round(s.w, 1), "closed"
            buckets[b].remove((i, r))
    # unclosed for longer than anything plausibly runs: the off was missed; drop it
    return [r for r in runs if not (r.running and now_ts - r.start_ts > MAX_RUN_S)]


def resolve_open_runs(runs: list[Run], now_ts: float, min_s: float = 600, factor: float = 3) -> None:
    """An unclosed run stays "running" only while younger than factor x its pattern's median closed
    duration (min_s when the pattern has never been seen closing); after that it's "unresolved"
    and adds no energy."""
    closed: dict[int, list[float]] = {}
    for r in runs:
        if r.status == "closed":
            closed.setdefault(r.pattern, []).append(r.duration_s(now_ts))
    for r in runs:
        if r.status == "closed":
            continue
        ds = sorted(closed.get(r.pattern, []))
        # with closed examples: 3x their median (a 13 s pulse isn't "on" after a minute); without: min_s
        limit = max(30.0, factor * ds[len(ds) // 2]) if ds else min_s
        r.status = "running" if now_ts - r.start_ts <= limit else "unresolved"


class Patterns:
    """Persistent pattern centres and user names, in settings.db."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, legs TEXT NOT NULL, stages INTEGER NOT NULL,
                center_w REAL NOT NULL, n INTEGER NOT NULL DEFAULT 0, name TEXT, ignored INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, named_at REAL, last_seen REAL)""")
            cols = {r[1] for r in db.execute("PRAGMA table_info(patterns)")}
            if "last_seen" not in cols:
                db.execute("ALTER TABLE patterns ADD COLUMN last_seen REAL")

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

    def assign(self, runs: list[Run]) -> None:
        """Give every run a pattern id, creating patterns as needed. Named patterns' centres stay put."""
        with self._conn() as db:
            pats = self._snapshot(db)
            for r in runs:
                stages = min(len(r.stages), 2)  # 1 = single step, 2 = multi-stage
                best, best_err = None, None
                for p in pats:
                    if p["legs"] != r.legs or p["stages"] != stages:
                        continue
                    err = abs(r.w - p["center_w"]) / p["center_w"]
                    if err <= CLUSTER_TOL and (best_err is None or err < best_err):
                        best, best_err = p, err
                if best is None:
                    cur = db.execute("INSERT INTO patterns (legs, stages, center_w, n, created) VALUES (?,?,?,?,?)",
                                     (r.legs, stages, r.w, 0, time.time()))
                    best = {"id": cur.lastrowid, "legs": r.legs, "stages": stages, "center_w": r.w, "n": 0, "name": None}
                    pats.append(best)
                r.pattern = best["id"]
            # refresh centres (unnamed only) and counts from this window's runs
            by_id: dict[int, list[Run]] = {}
            for r in runs:
                by_id.setdefault(r.pattern, []).append(r)
            for p in pats:
                rs = by_id.get(p["id"], [])
                if not rs:
                    db.execute("UPDATE patterns SET n=0 WHERE id=?", (p["id"],))
                    continue
                if not p.get("name"):
                    p["center_w"] = sorted(x.w for x in rs)[len(rs) // 2]  # median
                # the name guard is in SQL: a name set by the API after our snapshot still freezes the centre
                db.execute("UPDATE patterns SET center_w = CASE WHEN name IS NULL THEN ? ELSE center_w END, n=?, last_seen=? WHERE id=?",
                           (p["center_w"], len(rs), max(x.start_ts for x in rs), p["id"]))
            # one-off sizes (noise, rare coincidences) would otherwise pile up forever
            db.execute("DELETE FROM patterns WHERE name IS NULL AND ignored=0 AND n=0 AND coalesce(last_seen, created) < ?",
                       (time.time() - PRUNE_AFTER_S,))

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


def detect_runs(deltas: list[dict], now_ts: float) -> list[Run]:
    """Steps and runs per frame-counter generation (frames only compare within one), combined.
    A run spanning a monitor reboot can't close and expires like any missed off."""
    by_gen: dict[int, list[dict]] = {}
    for d in deltas:
        by_gen.setdefault(d.get("generation", 0), []).append(d)
    runs: list[Run] = []
    for gen in sorted(by_gen):
        for r in pair_runs(to_steps(by_gen[gen]), now_ts):
            r.generation = gen
            runs.append(r)
    return runs


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
        self.deltas_db = deltas_db
        self.patterns = Patterns(settings_db)
        self.runs: list[Run] = []
        self.hints: dict[int, dict] = {}
        self.computed_at = 0.0

    def recompute(self, now_ts: float | None = None) -> None:
        now_ts = now_ts if now_ts is not None else time.time()
        runs = detect_runs(load_deltas(self.deltas_db, now_ts - WINDOW_S), now_ts)
        self.patterns.assign(runs)
        resolve_open_runs(runs, now_ts)
        try:
            self.hints = sense_hints(runs, load_sense_ons(self.deltas_db, now_ts - WINDOW_S))
        except sqlite3.Error:
            self.hints = {}
        self.runs, self.computed_at = runs, now_ts

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
                                             "last_ts": 0.0, "running": False, "example": None})
            s["runs_window"] += 1
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
                     "running": False, "example": None}
            ds = sorted(s.pop("durations"))
            out.append({**p, **s, "sense_hint": self.hints.get(p["id"]), "wh_today": round(s["wh_today"], 1), "wh_window": round(s["wh_window"], 1),
                        "median_duration_s": round(ds[len(ds) // 2], 1) if ds else None, "closed_runs": len(ds)})
        out.sort(key=lambda x: (-x["wh_today"], -x["runs_window"]))
        return out
