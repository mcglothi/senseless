"""SQLite storage for the realtime feed.

Time is stored three ways: `ts` (Sense's `epoch`, unix seconds), `recv_ts` (local receive time) and
`frame` (the monitor's AC-cycle counter, 60/s). Frames are what to use for step timing.

`generation` guards the frame key: it increments whenever `frame` jumps backwards (monitor reboot or
counter reset, behavior not yet observed), so a reset never collides with earlier rows.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS readings (
    generation INTEGER NOT NULL DEFAULT 0,
    frame      INTEGER NOT NULL,
    ts         REAL NOT NULL,
    recv_ts    REAL,
    w          REAL, l1_w REAL, l2_w REAL,
    l1_v       REAL, l2_v REAL, hz REAL,
    grid_w     REAL, solar_w REAL,
    PRIMARY KEY (generation, frame)
);
CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts);

-- Step events detected on the monitor itself (realtime_update.deltas).
CREATE TABLE IF NOT EXISTS deltas (
    generation  INTEGER NOT NULL DEFAULT 0,
    start_frame INTEGER NOT NULL,
    frame       INTEGER NOT NULL,
    channel     INTEGER NOT NULL,      -- 0 = L1, 1 = L2
    w           REAL NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (generation, start_frame, channel, w)
);
CREATE INDEX IF NOT EXISTS deltas_ts ON deltas(ts);

-- Sense's own per-device attribution; only written when the set/values change.
CREATE TABLE IF NOT EXISTS device_power (
    generation INTEGER NOT NULL DEFAULT 0,
    frame      INTEGER NOT NULL,
    ts         REAL NOT NULL,
    device_id  TEXT NOT NULL,
    name       TEXT,
    w          REAL,
    PRIMARY KEY (generation, frame, device_id)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""

# A backwards jump bigger than this (frames) means a new counter generation, not reordering.
RESET_THRESHOLD = 60 * 60


def _pair(v) -> tuple:
    v = v if isinstance(v, (list, tuple)) else []
    return (v[0] if len(v) > 0 else None, v[1] if len(v) > 1 else None)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
        self.generation = int(row[0]) if row else 0
        row = self.db.execute("SELECT max(frame) FROM readings WHERE generation=?", (self.generation,)).fetchone()
        self.last_frame = row[0]
        self._last_devices: dict[str, int] = {}
        self._names: dict[str, str] = {}

    def _check_generation(self, frame: int) -> None:
        if self.last_frame is not None and frame < self.last_frame - RESET_THRESHOLD:
            self.generation += 1
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('generation', ?)", (str(self.generation),))
            self._last_devices.clear()
        if self.last_frame is None or frame > self.last_frame or frame < self.last_frame - RESET_THRESHOLD:
            self.last_frame = frame

    def realtime(self, p: dict, recv_ts: float) -> None:
        frame = int(p["frame"])
        self._check_generation(frame)
        g = self.generation
        ts = float(p.get("epoch") or recv_ts)
        l1_w, l2_w = _pair(p.get("channels"))
        l1_v, l2_v = _pair(p.get("voltage"))
        self.db.execute(
            "INSERT OR IGNORE INTO readings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (g, frame, ts, recv_ts, p.get("w"), l1_w, l2_w, l1_v, l2_v, p.get("hz"), p.get("grid_w"), p.get("solar_w")),
        )
        for d in p.get("deltas") or []:
            if not isinstance(d, dict) or d.get("w") is None or d.get("channel") is None:
                continue
            start = d.get("start_frame", d.get("frame"))
            self.db.execute(
                "INSERT OR IGNORE INTO deltas VALUES (?,?,?,?,?,?)",
                (g, start, d.get("frame", start), d["channel"], d["w"], ts),
            )
        # Per-device watts jitter constantly; record only changes > 5 W.
        devs = {d["id"]: d for d in p.get("devices") or [] if isinstance(d, dict) and d.get("id")}
        cur = {i: round(d.get("w") or 0) for i, d in devs.items()}
        changed = {i for i, w in cur.items() if abs(w - self._last_devices.get(i, -999)) > 5}
        gone = set(self._last_devices) - set(cur)
        self._names.update({i: d.get("name") for i, d in devs.items()})
        for i in changed:
            self.db.execute(
                "INSERT OR IGNORE INTO device_power VALUES (?,?,?,?,?,?)",
                (g, frame, ts, i, devs[i].get("name"), devs[i].get("w")),
            )
        for i in gone:
            self.db.execute("INSERT OR IGNORE INTO device_power VALUES (?,?,?,?,?,0)", (g, frame, ts, i, self._names.get(i)))
        self._last_devices = {**{i: w for i, w in self._last_devices.items() if i in cur}, **{i: cur[i] for i in changed}}

    def event(self, type_: str, payload, recv_ts: float) -> None:
        self.db.execute("INSERT INTO events (ts, type, payload) VALUES (?,?,?)", (recv_ts, type_, json.dumps(payload)))

    def prune_readings(self, before_ts: float) -> int:
        n = self.db.execute("DELETE FROM readings WHERE ts < ?", (before_ts,)).rowcount
        self.db.commit()
        return n

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
