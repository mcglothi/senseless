"""Long-running realtime feed collector: Sense WebSocket -> SQLite (+ raw jsonl.gz archive).

Recovery model (meant to run unattended):
- No realtime_update for FEED_STALE_S  -> drop the socket and reconnect.
- Feed unhealthy for EXIT_AFTER_S      -> exit non-zero so the container restart policy takes over.
- Handshake 401/403                    -> re-authenticate; other failures just back off.
- A bad message is counted and skipped; it never kills the loop.
"""

import asyncio
import gzip
import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta
from pathlib import Path

import websockets

from . import auth, web
from .state import LiveState
from .store import Store

log = logging.getLogger("senseless.collector")
WS = "wss://clientrt.sense.com/monitors/{monitor_id}/realtimefeed?access_token={token}"

FEED_STALE_S = float(os.environ.get("SENSELESS_FEED_STALE_S", "60"))
EXIT_AFTER_S = float(os.environ.get("SENSELESS_EXIT_AFTER_S", "900"))
COMMIT_EVERY_S = 5.0
RAW_RETENTION_DAYS = int(os.environ.get("SENSELESS_RAW_RETENTION_DAYS", "90"))
READINGS_RETENTION_DAYS = int(os.environ.get("SENSELESS_READINGS_RETENTION_DAYS", "0"))  # 0 = keep forever


class RawArchive:
    """Daily-rotated gzip jsonl of every message, for replaying into future detectors."""

    def __init__(self, root: Path):
        self.root = root
        self.day = None
        self.f = None

    def write(self, recv_ts: float, text: str) -> None:
        day = datetime.fromtimestamp(recv_ts).strftime("%Y-%m-%d")
        if day != self.day:
            self.close()
            self.root.mkdir(parents=True, exist_ok=True)
            self.f = gzip.open(self.root / f"rt-{day}.jsonl.gz", "at")
            self.day = day
            self.prune()
        # Store the raw text (not re-serialized) so malformed messages are kept too.
        self.f.write(json.dumps({"t": recv_ts, "raw": text}) + "\n")

    def prune(self) -> None:
        if RAW_RETENTION_DAYS <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=RAW_RETENTION_DAYS)).strftime("%Y-%m-%d")
        for f in self.root.glob("rt-*.jsonl.gz"):
            if f.name[3:13] < cutoff:
                f.unlink()
                log.info("pruned %s", f.name)

    def flush(self) -> None:
        if self.f:
            self.f.flush()

    def close(self) -> None:
        if self.f:
            self.f.close()
            self.f = None


class Collector:
    def __init__(self, data_dir: Path, archive: bool = True):
        self.state = LiveState()
        self.store = Store(data_dir / "senseless.db")
        self.raw = RawArchive(data_dir / "raw") if archive else None
        self.sess: auth.Session | None = None
        self.ws = None
        self.last_rt_mono = 0.0  # monotonic time of last realtime_update
        self.unhealthy_since = time.monotonic()
        self.stopping = asyncio.Event()

    # ── message handling ────────────────────────────────────────────────────
    def handle(self, text: str, now: float) -> None:
        st = self.state
        if self.raw:
            self.raw.write(now, text)
        try:
            msg = json.loads(text)
            t, p = msg.get("type"), msg.get("payload")
            if t == "realtime_update":
                self.store.realtime(p, now)
                st.latest = p
                st.last_message_at = now
                self.last_rt_mono = time.monotonic()
                for d in p.get("deltas") or []:
                    st.deltas[(web.LEGS[d["channel"]] if d.get("channel") in (0, 1) else "?",
                               "up" if d.get("w", 0) > 0 else "down")] += 1
            else:
                if t == "hello" and isinstance(p, dict):
                    st.monitor_online = bool(p.get("online"))
                if t == "error":
                    log.warning("feed error message: %s", str(p)[:300])
                self.store.event(t or "?", p, now)
            st.messages[t or "?"] += 1
        except Exception as e:  # noqa: BLE001 — one bad message must not stop collection
            st.messages["rejected"] += 1
            log.warning("rejected message (%s: %s): %.200s", type(e).__name__, e, text)

    # ── connection loop ─────────────────────────────────────────────────────
    async def authenticate(self, fresh: bool) -> None:
        backoff = 5
        while not self.stopping.is_set():
            try:
                self.sess = await asyncio.to_thread(auth.session, fresh)
                log.info("authenticated (%s)", "fresh login" if fresh else "cached token or login")
                return
            except auth.AuthError as e:
                log.error("authentication failed: %s (retry in %ss)", e, backoff)
            except Exception as e:  # network, bitwarden, json…
                log.error("authentication error: %r (retry in %ss)", e, backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, 600)

    async def connect_loop(self) -> None:
        await self.authenticate(fresh=False)
        backoff = 1
        while not self.stopping.is_set():
            reauth = False
            try:
                url = WS.format(monitor_id=self.sess.monitor_id, token=self.sess.access_token)
                # Token goes in the query only: adding Authorization / X-Sense headers makes the handshake hang.
                async with websockets.connect(url, open_timeout=20, ping_interval=30, max_size=2**22) as ws:
                    self.ws = ws
                    self.state.connected = True
                    self.last_rt_mono = time.monotonic()
                    log.info("connected to monitor %s", self.sess.monitor_id)
                    backoff = 1
                    async for text in ws:
                        self.handle(text if isinstance(text, str) else text.decode("utf-8", "replace"), time.time())
            except websockets.InvalidStatus as e:
                code = e.response.status_code
                reauth = code in (401, 403)
                log.warning("handshake rejected: HTTP %s", code)
            except (OSError, TimeoutError, websockets.WebSocketException) as e:
                log.warning("connection lost: %r", e)
            finally:
                self.ws = None
                if self.state.connected:
                    self.state.reconnects += 1
                self.state.connected = False
                self.store.commit()
            if self.stopping.is_set():
                break
            if reauth:
                await self.authenticate(fresh=True)
            log.info("reconnecting in %ss", backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, 300)

    # ── housekeeping ────────────────────────────────────────────────────────
    async def watchdog(self) -> None:
        last_prune = 0.0
        while not self.stopping.is_set():
            await self._sleep(COMMIT_EVERY_S)
            self.store.commit()
            if self.raw:
                self.raw.flush()
            mono = time.monotonic()
            if self.ws is not None and mono - self.last_rt_mono > FEED_STALE_S:
                log.warning("no realtime_update for %.0fs; forcing reconnect", mono - self.last_rt_mono)
                await self.ws.close()
            if self.state.healthy():
                self.unhealthy_since = mono
            elif mono - self.unhealthy_since > EXIT_AFTER_S:
                log.error("feed unhealthy for %.0fs; exiting for restart", mono - self.unhealthy_since)
                self.store.close()
                os._exit(3)
            if READINGS_RETENTION_DAYS > 0 and mono - last_prune > 3600:
                last_prune = mono
                n = self.store.prune_readings(time.time() - READINGS_RETENTION_DAYS * 86400)
                if n:
                    log.info("pruned %d readings older than %d days", n, READINGS_RETENTION_DAYS)

    async def _sleep(self, s: float) -> None:
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=s)
        except TimeoutError:
            pass

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stopping.set)
        runner = await web.serve(self.state, self.store.path)
        tasks = [asyncio.create_task(self.connect_loop()), asyncio.create_task(self.watchdog())]
        await self.stopping.wait()
        log.info("shutting down")
        if self.ws is not None:
            await self.ws.close()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()
        self.store.close()
        if self.raw:
            self.raw.close()
        log.info("clean shutdown")


async def run(data_dir: Path, archive: bool = True) -> None:
    await Collector(data_dir, archive).run()
