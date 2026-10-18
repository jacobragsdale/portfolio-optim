"""Datasets, their cache policies, and the `Loader` that fetches them through their sources, validates them and caches
them in Redis."""

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import partial
from itertools import batched

import pandas as pd
import redis

from optim.cache import RedisCache
from optim.data.frames import empty_frame, validate_columns
from optim.logs import brief, timed, to_json
from optim.schema import Row
from optim.sources import Columns, Param, Source, SourceName
from optim.sources.sql import read_sql

log = logging.getLogger(__name__)
_REDIS_DOWN_LOCK = threading.Lock()  # so the first loader thread to find Redis down is the one that says so


@dataclass(frozen=True)
class CachePolicy:
    """How a dataset uses the Redis cache. A dataset without one is read live on every load."""

    ttl: timedelta  # how long an entry lives; every query param is in the key, so a new business date is a new entry
    warm: Callable[[Loader, date], Iterable[Mapping[str, Param]]] | None = None  # the params `optim warm` loads, one entry each
    live_on_miss: bool = True  # on a miss, or with Redis down, read the source; False fails the run instead

    def __post_init__(self) -> None:
        if self.ttl <= timedelta(0):
            msg = f"cache ttl must be positive, got {self.ttl}"
            raise ValueError(msg)
        if not (self.live_on_miss or self.warm):
            msg = "live_on_miss=False needs a warm hook: nothing else would fill the cache"
            raise ValueError(msg)


def by_date(loader: Loader, as_of: date) -> list[dict[str, Param]]:  # noqa: ARG001
    """The usual warm hook: one entry for the business date, for a query that binds only `as_of`."""
    return [{"as_of": as_of}]


@dataclass(frozen=True)
class Dataset:
    """One table: its row schema, where it comes from, its key, and how it is cached."""

    name: str
    row: type[Row]
    source: SourceName
    query: str  # a SQL file's path inside the optim package, or a REST path
    key: tuple[str, ...]  # columns that must be unique
    cache: CachePolicy | None = None  # None: always read live
    check: Callable[[pd.DataFrame], None] | None = None  # cross-row rules; raise ValueError

    def to_frame(self, columns: Columns) -> pd.DataFrame:
        """Raw columns to this dataset's validated, typed frame, with its key and cross-row rules checked."""
        frame = validate_columns(self.row, columns, name=self.name, key=self.key)
        if (duplicated := frame.duplicated(list(self.key))).any():
            examples = frame.loc[duplicated, list(self.key)].head(3).to_dict("records")
            msg = f"{self.name}: duplicate {self.key}, e.g. {examples}"
            raise ValueError(msg)
        if self.check is not None:
            self.check(frame)
        return frame


@dataclass(frozen=True)
class Loader:
    """Fetches datasets through their sources, reading and filling the cache as each dataset's policy says. Without a
    cache, every dataset is read live. Thread-safe."""

    sources: Mapping[SourceName, Source]
    cache: RedisCache | None = None
    refresh: bool = False  # set by `warm`: read live, overwrite the cache, and fail if Redis does
    _redis_down: threading.Event = field(default_factory=threading.Event, init=False, repr=False, compare=False)  # see `_cached`

    def load(self, dataset: Dataset, **params: Param) -> pd.DataFrame:
        try:
            with timed(log, "loaded", dataset=dataset.name, params=brief(params)) as fields:
                frame, fields["cache"] = self._read(dataset, params)
                fields["rows"] = len(frame)
        except Exception as error:
            error.add_note(f"while loading dataset {dataset.name!r} ({dataset.query}) from {dataset.source} with {to_json(brief(params))}")
            raise
        return frame

    def _read(self, dataset: Dataset, params: Mapping[str, Param]) -> tuple[pd.DataFrame, str]:
        """The frame, and where it came from: `live` (not cached), `hit`, `miss`, or `refresh` (warm)."""
        policy, cache = dataset.cache, self.cache
        if policy is None or cache is None:
            return self._fetch(dataset, params), "live"
        key = _cache_key(dataset, params)
        if self.refresh:
            frame = self._fetch(dataset, params)
            if frame.empty:
                msg = f"{dataset.name} returned no rows; an empty entry would hide the data for {policy.ttl} once it lands"
                raise ValueError(msg)
            cache.set(key, frame, policy.ttl)
            return frame, "refresh"
        if (hit := self._cached(dataset, partial(cache.get, key))) is not None:
            return hit, "hit"
        if not policy.live_on_miss:  # a miss, Redis down included, and the policy decides what happens next
            msg = f"{dataset.name} is not in the cache for {to_json(brief(params))}, and its policy forbids reading it live: check that Redis is up and `optim warm` ran"
            raise LookupError(msg)
        if policy.warm and not self._redis_down.is_set():  # warm should have filled it: the warm failed, or this load's params differ from the hook's
            log.warning("%s is warmed but missed the cache; reading it live", dataset.name, extra={"params": brief(params)})
        frame = self._fetch(dataset, params)
        if not frame.empty:  # usually a feed that has not landed yet: cached, it would hide the data for a whole ttl
            self._cached(dataset, partial(cache.set, key, frame, policy.ttl))
        return frame, "miss"

    def _cached[T](self, dataset: Dataset, call: Callable[[], T]) -> T | None:
        """A cache read or write that cannot fail the run, since the cache only saves time: an error is logged and reads as
        a miss, and the live read then overwrites a bad entry. The first time Redis is unavailable, the cache is off for the
        rest of the run, so an outage costs one timeout, not two per load."""
        if self._redis_down.is_set():
            return None
        try:
            return call()
        except redis.ConnectionError, redis.TimeoutError:  # AuthenticationError is a ConnectionError
            with _REDIS_DOWN_LOCK:
                first = not self._redis_down.is_set()
                self._redis_down.set()
            if first:
                log.warning("Redis is unavailable; reading every dataset live for the rest of this run", exc_info=True)
        except Exception:  # a corrupt entry, a key holding another type, Redis out of memory: only this key misses
            log.warning("could not use the cache for %s; reading it live", dataset.name, exc_info=True)
        return None

    def warm(self, datasets: Iterable[Dataset], as_of: date) -> bool:
        """`optim warm`: read each dataset live with every params its warm hook returns, and overwrite those entries. A
        failed load is logged and the rest still land; returns whether all of them did."""
        loader = replace(self, refresh=True)
        loads = [(d, params) for d in datasets if d.cache and d.cache.warm for params in d.cache.warm(loader, as_of)]
        with ThreadPoolExecutor() as pool:
            futures = {pool.submit(loader.load, d, **params): (d.name, params) for d, params in loads}
        failed = [(name, params, error) for future, (name, params) in futures.items() if (error := future.exception())]
        for name, params, error in failed:
            log.error("could not warm %s", name, extra={"params": brief(params)}, exc_info=error)
        log.info("warmed %d of %d entries", len(futures) - len(failed), len(futures))
        return not failed

    def load_all(self, *datasets: Dataset, **params: Param) -> dict[str, pd.DataFrame]:
        """Load independent datasets concurrently, keyed by dataset name."""
        with ThreadPoolExecutor() as pool:
            futures = {dataset.name: pool.submit(self.load, dataset, **params) for dataset in datasets}
        return {name: future.result() for name, future in futures.items()}

    def load_batched(self, dataset: Dataset, param: str, ids: Sequence[str], *, size: int = 1000, **params: Param) -> pd.DataFrame:
        """Load `ids` in chunks of `size`, one query per chunk, binding each chunk to `param`."""
        frames = [self.load(dataset, **params, **{param: chunk}) for chunk in batched(ids, size, strict=False)]
        return pd.concat(frames, ignore_index=True) if frames else empty_frame(dataset.row)

    def _fetch(self, dataset: Dataset, params: Mapping[str, Param]) -> pd.DataFrame:
        columns = self.sources[dataset.source].fetch(dataset.query, params)
        with timed(log, "validated", dataset=dataset.name):
            return dataset.to_frame(columns)


def _cache_key(dataset: Dataset, params: Mapping[str, Param]) -> str:
    """Changes whenever the params, the SQL or the row schema change, so a deploy never reads what older code cached."""
    query = read_sql(dataset.query) if dataset.query.endswith(".sql") else dataset.query  # a REST path is keyed as given
    payload = json.dumps([json.dumps(dataset.row.model_json_schema(), sort_keys=True), query, params], sort_keys=True, default=str)
    return f"{dataset.name}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
