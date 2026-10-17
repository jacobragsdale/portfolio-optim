"""Shared cache across processes and machines. Frames are stored as Parquet, so dtypes such as ordered categories and
dates survive the round trip."""

import io
from dataclasses import dataclass
from datetime import timedelta

import pandas as pd
import redis


@dataclass(frozen=True)
class RedisCache:
    client: redis.Redis
    prefix: str = "optim:"

    def get(self, key: str) -> pd.DataFrame | None:
        data = self.client.get(self.prefix + key)
        return pd.read_parquet(io.BytesIO(data)) if isinstance(data, bytes) else None

    def set(self, key: str, frame: pd.DataFrame, ttl: timedelta) -> None:
        self.client.set(self.prefix + key, frame.to_parquet(index=False), ex=ttl)
