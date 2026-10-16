"""In-memory live state shared between the collector and the HTTP server."""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field


CLOSE = {"type": "_close"}  # sentinel: ends a stream handler


@dataclass
class LiveState:
    connected: bool = False
    monitor_online: bool | None = None
    last_message_at: float = 0.0  # last realtime_update (other message types don't count)
    started_at: float = field(default_factory=time.time)
    latest: dict = field(default_factory=dict)  # last realtime_update payload
    messages: Counter = field(default_factory=Counter)  # by type
    deltas: Counter = field(default_factory=Counter)  # by (leg, direction)
    reconnects: int = 0
    subscribers: set = field(default_factory=set)  # asyncio.Queue per /api/stream client

    def message_age(self) -> float | None:
        return time.time() - self.last_message_at if self.last_message_at else None

    def healthy(self, max_age: float = 30.0) -> bool:
        age = self.message_age()
        return self.connected and age is not None and age < max_age

    def close_streams(self) -> None:
        """Tell every /api/stream handler to finish (shutdown): open streams otherwise block runner cleanup."""
        for q in list(self.subscribers):
            try:
                q.put_nowait(CLOSE)
            except asyncio.QueueFull:
                q.get_nowait()
                q.put_nowait(CLOSE)

    def publish(self, event: dict) -> None:
        """Fan an event out to every live stream client; a slow client drops events, never blocks the collector."""
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
