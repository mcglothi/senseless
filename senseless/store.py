"""SQLite storage for the realtime feed.

Time is stored two ways: `ts` (wall clock, unix seconds from Sense's epoch
field or local receive time) and `frame` (the monitor's AC-cycle counter,
60/s). Frames are what to use for step timing; ts is for humans and joins.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    frame   INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    w       REAL, l1_w REAL, l2_w REAL,
    l1_v    REAL, l2_v REAL, hz REAL,
    grid_w  REAL, solar_w REAL
);
CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts);

-- Step events detected on the monitor itself (realtime_update.deltas).
CREATE TABLE IF NOT EXISTS deltas (
    start_frame INTEGER NOT NULL,
    frame       INTEGER NOT NULL,
    channel     INTEGER NOT NULL,      -- 0 = L1, 1 = L2
    w           REAL NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (start_frame, channel, w)
);
CREATE INDEX IF NOT EXISTS deltas_ts ON deltas(ts);

-- Sense's own per-device attribution; only written when the set/values change.
CREATE TABLE IF NOT EXISTS device_power (
    frame     INTEGER NOT NULL,
    ts        REAL NOT NULL,
    device_id TEXT NOT NULL,
    name      TEXT,
    w         REAL,
    PRIMARY KEY (frame, device_id)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._last_devices: dict[str, int] = {}
        self._names: dict[str, str] = {}

    def realtime(self, p: dict, recv_ts: float) -> None:
        frame = p["frame"]
        ts = float(p.get("epoch") or recv_ts)
        ch = p.get("channels") or [None, None]
        v = p.get("voltage") or [None, None]
        self.db.execute(
            "INSERT OR IGNORE INTO readings VALUES (?,?,?,?,?,?,?,?,?,?)",
            (frame, ts, p.get("w"), ch[0], ch[1], v[0], v[1], p.get("hz"), p.get("grid_w"), p.get("solar_w")),
        )
        for d in p.get("deltas") or []:
            self.db.execute(
                "INSERT OR IGNORE INTO deltas VALUES (?,?,?,?,?)",
                (d["start_frame"], d["frame"], d["channel"], d["w"], ts),
            )
        # Per-device watts jitter constantly; record only changes > 5 W.
        devs = {d["id"]: d for d in p.get("devices") or []}
        cur = {i: round(d.get("w", 0)) for i, d in devs.items()}
        changed = {i for i, w in cur.items() if abs(w - self._last_devices.get(i, -999)) > 5}
        gone = set(self._last_devices) - set(cur)
        self._names.update({i: d.get("name") for i, d in devs.items()})
        for i in changed:
            self.db.execute(
                "INSERT OR IGNORE INTO device_power VALUES (?,?,?,?,?)",
                (frame, ts, i, devs[i].get("name"), devs[i].get("w")),
            )
        for i in gone:
            self.db.execute("INSERT OR IGNORE INTO device_power VALUES (?,?,?,?,0)", (frame, ts, i, self._names.get(i)))
        self._last_devices = {**{i: w for i, w in self._last_devices.items() if i in cur}, **{i: cur[i] for i in changed}}

    def event(self, type_: str, payload, recv_ts: float) -> None:
        self.db.execute("INSERT INTO events (ts, type, payload) VALUES (?,?,?)", (recv_ts, type_, json.dumps(payload)))

    def commit(self) -> None:
        self.db.commit()
