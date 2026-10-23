"""The `Loader`, which fetches datasets (`optim.data.dataset`) through their sources, validates them and caches them in
Redis. Loading is raw acquisition: a frame holds what its source returned, validated against its row model and nothing
more. Combining frames into what a solve sees is prep (`optim.prep`)."""

import hashlib
import json
import logging
import sys
import threading
import time
from collections.abc import Callable, Generator, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import partial
from typing import Literal, Self

import pandas as pd
import redis

from optim.data.cache import RedisCache
from optim.data.dataset import Dataset
from optim.logs import brief, ms_since, timed, to_json, utc_now
from optim.schema import Model
from optim.sources import Param, Source, SourceName
from optim.sources.sql import read_sql

log = logging.getLogger(__name__)
_REDIS_DOWN_LOCK = threading.Lock()  # so the first loader thread to find Redis down is the one that says so
_stop: ContextVar[threading.Event | None] = ContextVar("_stop", default=None)  # set when a call in a `TaskGroup` fails


class Skipped(Exception):  # noqa: N818 -- not an error: the call was not made
    """A load that never reached its source, because another load in the same `TaskGroup` had already failed."""


class TaskGroup:
    """Runs each submitted call in a thread and waits for them all on exit, like asyncio's TaskGroup. The first failure
    stops the rest: loads still waiting for a source slot are skipped, and those already running end at their source's
    timeout. Then the failure raises. Nested groups share the stop, so a failed batch also stops the market loading
    beside it."""

    def __init__(self) -> None:
        self._stop = _stop.get() or threading.Event()
        self._errors: list[BaseException] = []
        # a thread per call, mostly waiting on a source's slot; fine to thousands of batches, a queue beyond
        self._pool = ThreadPoolExecutor(max_workers=sys.maxsize)

    def submit[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        def run() -> T:
            _stop.set(self._stop)  # the pool is this group's alone, so its threads never carry another group's stop
            try:
                return fn(*args, **kwargs)
            except BaseException as error:
                self._errors.append(error)
                self._stop.set()
                raise

        return self._pool.submit(run)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self._pool.shutdown()
        if self._errors:
            raise next((e for e in self._errors if not isinstance(e, Skipped)), self._errors[0])  # the cause, not the loads it skipped


class DatasetRead(Model):
    """How a run got one dataset, as its snapshot's `run.json` records it."""

    read: Literal["live", "hit", "miss", "refresh"]
    """`live`: read from its source, as it is not cached; `hit`: from the cache; `miss`: read from its source, then
    cached; `refresh`: read from its source by `optim warm`."""
    fetched_at: datetime
    """When its source was read: for a hit, when the cache entry was filled. Loaded in batches, the oldest batch's."""


class Reads:
    """Where each dataset of a run came from, as its loads finish. Thread-safe: they finish concurrently."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_dataset: dict[str, DatasetRead] = {}

    def add(self, dataset: str, read: DatasetRead) -> None:
        """A dataset loaded in batches keeps its oldest read."""
        with self._lock:
            if (seen := self._by_dataset.get(dataset)) is None or read.fetched_at < seen.fetched_at:
                self._by_dataset[dataset] = read

    def by_dataset(self) -> dict[str, DatasetRead]:
        """By dataset name, so a run's `run.json` lists them in the same order whichever load finished first."""
        with self._lock:
            return dict(sorted(self._by_dataset.items()))


@dataclass(frozen=True)
class Loader:
    """Fetches datasets through their sources, reading and filling the cache as each dataset's policy says. Without a
    cache, every dataset is read live. Thread-safe."""

    sources: Mapping[SourceName, Source]
    cache: RedisCache | None = None
    limits: Mapping[SourceName, threading.Semaphore] = field(default_factory=dict)  # calls each source may serve at once; none: unbounded
    refresh: bool = False  # set by `warm`: read live, overwrite the cache, and fail if Redis does
    reads: Reads | None = None  # set for a run: where each load got its data, for the run's snapshot
    clock: Callable[[], datetime] = utc_now  # stamps each source read
    _redis_down: threading.Event = field(default_factory=threading.Event, init=False, repr=False, compare=False)  # see `_cached`

    def load(self, dataset: Dataset, **params: Param) -> pd.DataFrame:
        try:
            with timed(log, "loaded", dataset=dataset.name, params=brief(params)) as fields:
                frame, read = self._read(dataset, params, fields)
                fields["cache"], fields["rows"] = read.read, len(frame)
        except Exception as error:
            error.add_note(f"while loading dataset {dataset.name!r} ({dataset.query}) from {dataset.source} with {to_json(brief(params))}")
            raise
        if self.reads is not None:
            self.reads.add(dataset.name, read)
        return frame

    def _read(self, dataset: Dataset, params: Mapping[str, Param], fields: dict[str, object]) -> tuple[pd.DataFrame, DatasetRead]:
        """The frame, and where it came from. A source read adds `wait_ms` to `fields`: the time spent waiting for a
        slot."""
        policy, cache = dataset.cache, self.cache
        if policy is None or cache is None:
            frame, fetched_at = self._fetch(dataset, params, fields)
            return frame, DatasetRead(read="live", fetched_at=fetched_at)
        key = _cache_key(dataset, params)
        if self.refresh:
            frame, fetched_at = self._fetch(dataset, params, fields)
            if frame.empty:
                msg = f"{dataset.name} returned no rows; an empty entry would hide the data for {policy.ttl} once it lands"
                raise ValueError(msg)
            cache.set(key, frame, fetched_at, policy.ttl)
            return frame, DatasetRead(read="refresh", fetched_at=fetched_at)
        if (hit := self._cached(dataset, partial(cache.get, key))) is not None:
            frame, fetched_at = hit
            return frame, DatasetRead(read="hit", fetched_at=fetched_at)
        if not policy.live_on_miss:  # a miss, Redis down included, and the policy decides what happens next
            msg = f"{dataset.name} is not in the cache for {to_json(brief(params))}, and its policy forbids reading it live: check that Redis is up and `optim warm` ran"
            raise LookupError(msg)
        if policy.warm and not self._redis_down.is_set():  # warm should have filled it: the warm failed, or this load's params differ from the hook's
            log.warning("%s is warmed but missed the cache; reading it live", dataset.name, extra={"params": brief(params)})
        frame, fetched_at = self._fetch(dataset, params, fields)
        if not frame.empty:  # usually a feed that has not landed yet: cached, it would hide the data for a whole ttl
            self._cached(dataset, partial(cache.set, key, frame, fetched_at, policy.ttl))
        return frame, DatasetRead(read="miss", fetched_at=fetched_at)

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

    def warm(self, datasets: Iterable[Dataset], as_of_date: date) -> bool:
        """`optim warm`: read each dataset live with every params its warm hook returns, and overwrite those entries. A
        failed load is logged and the rest still land; returns whether all of them did."""
        loader = replace(self, refresh=True)
        loads = [(d, params) for d in datasets if d.cache and d.cache.warm for params in d.cache.warm(loader, as_of_date)]
        with ThreadPoolExecutor() as pool:
            futures = {pool.submit(loader.load, d, **params): (d.name, params) for d, params in loads}
        failed = [(name, params, error) for future, (name, params) in futures.items() if (error := future.exception())]
        for name, params, error in failed:
            log.error("could not warm %s", name, extra={"params": brief(params)}, exc_info=error)
        log.info("warmed %d of %d entries", len(futures) - len(failed), len(futures))
        return not failed

    def load_all(self, *datasets: Dataset, **params: Param) -> dict[str, pd.DataFrame]:
        """Load independent datasets concurrently, keyed by dataset name."""
        with TaskGroup() as group:
            futures = {dataset.name: group.submit(self.load, dataset, **params) for dataset in datasets}
        return {name: future.result() for name, future in futures.items()}

    @contextmanager
    def read_only(self, source: SourceName) -> Generator[Loader]:
        """A loader whose loads from `source` share one read-only transaction, until the block ends, so reads that
        depend on each other, such as a portfolio's holdings and its cash flows, see the source at one moment. The
        transaction holds one of the source's slots throughout, so open connections never exceed its limit; its loads
        take turns on the connection, from whichever threads call them."""
        start = time.perf_counter()
        with self.limits.get(source) or nullcontext():
            _skip_if_stopped()
            with self.sources[source].read_only() as transaction:
                log.info("began read-only transaction", extra={"source": source, "wait_ms": ms_since(start)})
                yield replace(self, sources={**self.sources, source: transaction}, limits={k: v for k, v in self.limits.items() if k != source})

    def _fetch(self, dataset: Dataset, params: Mapping[str, Param], fields: dict[str, object]) -> tuple[pd.DataFrame, datetime]:
        """The frame, and when the source was asked for it, once a slot was free."""
        start = time.perf_counter()
        with self.limits.get(dataset.source) or nullcontext():
            fields["wait_ms"] = ms_since(start)
            _skip_if_stopped()
            fetched_at = self.clock()
            columns = self.sources[dataset.source].fetch(dataset.query, params)
        with timed(log, "validated", dataset=dataset.name):
            return dataset.to_frame(columns), fetched_at


def _skip_if_stopped() -> None:
    if (stop := _stop.get()) and stop.is_set():
        raise Skipped


def _cache_key(dataset: Dataset, params: Mapping[str, Param]) -> str:
    """Changes whenever the params, the SQL or the row schema change, so a deploy never reads what older code cached."""
    query = read_sql(dataset.query) if dataset.query.endswith(".sql") else dataset.query  # a REST path is keyed as given
    payload = json.dumps([json.dumps(dataset.row.model_json_schema(), sort_keys=True), query, params], sort_keys=True, default=str)
    return f"{dataset.name}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
