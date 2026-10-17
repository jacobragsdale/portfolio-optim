"""Optional caching of loaded datasets: in this process, or shared across processes and machines through Redis.

Each `Dataset.ttl` says whether and how long that dataset may be cached. Whether a run uses a cache at all is the
caller's choice: `Loader(sources, cache=None)` always reads live.
"""

import io
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

import pandas as pd
import redis


class Cache(Protocol):
    def get(self, key: str) -> pd.DataFrame | None: ...
    def set(self, key: str, frame: pd.DataFrame, ttl: timedelta) -> None: ...


@dataclass
class MemoryCache:
    """Per-process cache, for long-lived processes such as an API server. Workers in a pool do not share it."""

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


@dataclass(frozen=True)
class RedisCache:
    """Shared cache. Frames are stored as Parquet so dtypes (ordered categories, dates) survive the round trip."""

    client: redis.Redis
    prefix: str = "optim:"

    def get(self, key: str) -> pd.DataFrame | None:
        data = self.client.get(self.prefix + key)
        return pd.read_parquet(io.BytesIO(data)) if isinstance(data, bytes) else None

    def set(self, key: str, frame: pd.DataFrame, ttl: timedelta) -> None:
        self.client.set(self.prefix + key, frame.to_parquet(index=False), ex=ttl)
