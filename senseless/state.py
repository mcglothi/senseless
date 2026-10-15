"""In-memory live state shared between the collector and the HTTP server."""

import time
from collections import Counter
from dataclasses import dataclass, field


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

    def message_age(self) -> float | None:
        return time.time() - self.last_message_at if self.last_message_at else None

    def healthy(self, max_age: float = 30.0) -> bool:
        age = self.message_age()
        return self.connected and age is not None and age < max_age
