"""Per-process cache, for long-lived processes such as an API server. Workers in a pool do not share it."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd


@dataclass
class MemoryCache:
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, tuple[float, pd.DataFrame]] = field(default_factory=dict, init=False, repr=False)

    def get(self, key: str) -> pd.DataFrame | None:
        match self._entries.get(key):
            case (expires, frame) if expires > self.clock():
                return frame.copy(deep=False)  # copy-on-write: a caller's edits never reach the cached frame
            case _:
                return None

    def set(self, key: str, frame: pd.DataFrame, ttl: timedelta) -> None:
        self._entries[key] = (self.clock() + ttl.total_seconds(), frame)
