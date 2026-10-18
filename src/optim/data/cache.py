"""The dataset cache, in Redis: every Job is a new process, so only a shared cache saves a load. Each dataset's
`CachePolicy` says how it is cached. Frames are stored as Parquet, so dtypes such as ordered categories and dates survive
the round trip, with the time the source was read in the file's metadata, so a run can record how old a hit was."""

import io
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import redis

_FETCHED_AT = b"optim.fetched_at"


@dataclass(frozen=True)
class RedisCache:
    client: redis.Redis
    prefix: str = "optim:"

    def get(self, key: str) -> tuple[pd.DataFrame, datetime] | None:
        """The frame, and when its source was read."""
        data = self.client.get(self.prefix + key)
        return decode(data) if isinstance(data, bytes) else None

    def set(self, key: str, frame: pd.DataFrame, fetched_at: datetime, ttl: timedelta) -> None:
        self.client.set(self.prefix + key, encode(frame, fetched_at), ex=ttl)

    def clear(self, dataset: str) -> int:
        """Delete every entry of one dataset, whatever its params (keys are `<dataset>:<digest>`). Returns how many."""
        keys = list(self.client.scan_iter(match=f"{self.prefix}{dataset}:*", count=1000))
        if keys:
            self.client.unlink(*keys)  # frees the memory in the background
        return len(keys)


def encode(frame: pd.DataFrame, fetched_at: datetime) -> bytes:
    """`frame` as Parquet, written as `DataFrame.to_parquet` writes it, with `fetched_at` in the file's metadata."""
    table = pa.Table.from_pandas(frame, preserve_index=False)
    table = table.replace_schema_metadata((table.schema.metadata or {}) | {_FETCHED_AT: fetched_at.isoformat().encode()})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def decode(data: bytes) -> tuple[pd.DataFrame, datetime]:
    """An entry `encode` wrote. One without a fetch time, written by older code, raises: the loader reads it as a miss."""
    metadata = pq.read_schema(io.BytesIO(data)).metadata or {}
    if _FETCHED_AT not in metadata:
        msg = "the cache entry has no fetch time: older code wrote it"
        raise ValueError(msg)
    return pd.read_parquet(io.BytesIO(data)), datetime.fromisoformat(metadata[_FETCHED_AT].decode())
