"""Breaker map from Breaker Hunt: which breaker drops how much on which leg. Lives in settings.db."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class CircuitError(ValueError):
    pass


class Circuits:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS circuits (
                id INTEGER PRIMARY KEY AUTOINCREMENT, breaker TEXT NOT NULL, name TEXT,
                l1_w REAL NOT NULL, l2_w REAL NOT NULL, created REAL NOT NULL)""")

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
            return [dict(r) for r in db.execute("SELECT * FROM circuits ORDER BY created")]

    def add(self, body: dict) -> dict:
        breaker = body.get("breaker")
        name = body.get("name")
        if not isinstance(breaker, str) or not breaker.strip() or len(breaker) > 20:
            raise CircuitError("breaker must be a short label, e.g. \"14\"")
        if name is not None and (not isinstance(name, str) or len(name) > 60):
            raise CircuitError("name must be a string of at most 60 characters")
        watts = []
        for k in ("l1_w", "l2_w"):
            v = body.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not -20000 <= v <= 20000:
                raise CircuitError(f"{k} must be a number of watts")
            watts.append(round(float(v), 1))
        with self._conn() as db:
            cur = db.execute("INSERT INTO circuits (breaker, name, l1_w, l2_w, created) VALUES (?,?,?,?,?)",
                             (breaker.strip(), (name or "").strip() or None, watts[0], watts[1], time.time()))
            return dict(db.execute("SELECT * FROM circuits WHERE id=?", (cur.lastrowid,)).fetchone())

    def remove(self, circuit_id: int) -> None:
        with self._conn() as db:
            if not db.execute("DELETE FROM circuits WHERE id=?", (circuit_id,)).rowcount:
                raise KeyError(circuit_id)
