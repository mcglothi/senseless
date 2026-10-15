"""senseless CLI.

    senseless collect [--data DIR] [--no-archive]   run the realtime collector
    senseless deltas  [--data DIR] [--minutes N]    show recent step events
"""

import argparse
import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import collector

DEFAULT_DATA = Path(os.environ.get("SENSELESS_DATA", Path(__file__).resolve().parent.parent / "data"))


def cmd_deltas(data: Path, minutes: float) -> None:
    db = sqlite3.connect(data / "senseless.db")
    rows = db.execute(
        "SELECT ts, start_frame, channel, w FROM deltas WHERE ts > ? ORDER BY start_frame",
        (time.time() - minutes * 60,),
    ).fetchall()
    for ts, frame, ch, w in rows:
        print(f"{datetime.fromtimestamp(ts):%H:%M:%S}  L{ch + 1}  {w:+8.1f} W  frame {frame}")
    print(f"{len(rows)} deltas in the last {minutes:g} min")


def main() -> None:
    ap = argparse.ArgumentParser(prog="senseless")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--no-archive", action="store_true")
    d = sub.add_parser("deltas")
    d.add_argument("--minutes", type=float, default=10)
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.cmd == "collect":
        asyncio.run(collector.run(a.data, archive=not a.no_archive))
    elif a.cmd == "deltas":
        cmd_deltas(a.data, a.minutes)


if __name__ == "__main__":
    main()
