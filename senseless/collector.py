"""Long-running realtime feed collector: Sense WebSocket -> SQLite (+ raw jsonl.gz archive)."""

import asyncio
import gzip
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import websockets

from . import auth
from .store import Store

log = logging.getLogger("senseless.collector")
WS = "wss://clientrt.sense.com/monitors/{monitor_id}/realtimefeed?access_token={token}"


class RawArchive:
    """Daily-rotated gzip jsonl of every message, for replaying into future detectors."""

    def __init__(self, root: Path):
        self.root = root
        self.day = None
        self.f = None

    def write(self, recv_ts: float, msg: dict) -> None:
        day = datetime.fromtimestamp(recv_ts).strftime("%Y-%m-%d")
        if day != self.day:
            if self.f:
                self.f.close()
            self.root.mkdir(parents=True, exist_ok=True)
            self.f = gzip.open(self.root / f"rt-{day}.jsonl.gz", "at")
            self.day = day
        self.f.write(json.dumps({"t": recv_ts, "msg": msg}) + "\n")

    def flush(self) -> None:
        if self.f:
            self.f.flush()


async def run(data_dir: Path, archive: bool = True) -> None:
    store = Store(data_dir / "senseless.db")
    raw = RawArchive(data_dir / "raw") if archive else None
    sess = auth.session()
    backoff = 1
    while True:
        try:
            url = WS.format(monitor_id=sess.monitor_id, token=sess.access_token)
            # Token goes in the query only: adding Authorization / X-Sense headers makes the handshake hang.
            async with websockets.connect(url, open_timeout=20, ping_interval=30, max_size=None) as ws:
                log.info("connected to monitor %s", sess.monitor_id)
                backoff = 1
                last_commit = time.time()
                async for text in ws:
                    now = time.time()
                    msg = json.loads(text)
                    if raw:
                        raw.write(now, msg)
                    t, p = msg.get("type"), msg.get("payload")
                    if t == "realtime_update":
                        store.realtime(p, now)
                    elif t == "error":
                        log.warning("feed error: %s", p)
                        if "auth" in json.dumps(p).lower():
                            sess = auth.session(fresh=True)
                            break
                    else:
                        store.event(t, p, now)
                    if now - last_commit > 5:
                        store.commit()
                        if raw:
                            raw.flush()
                        last_commit = now
        except websockets.InvalidStatus as e:
            log.warning("handshake rejected (%s); re-authenticating", e.response.status_code)
            sess = auth.session(fresh=True)
        except (OSError, TimeoutError, websockets.ConnectionClosed) as e:
            log.warning("connection lost: %r", e)
        store.commit()
        log.info("reconnecting in %ss", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)
