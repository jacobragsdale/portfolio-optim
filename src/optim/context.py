"""The process's connections, opened once and shared by every job: the CLI opens one per command, and an API server
would open one in its lifespan handler. Every source, cache and client is wired here and nowhere else."""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import redis

from optim.cache import Cache
from optim.cache.memory import MemoryCache
from optim.cache.redis import RedisCache
from optim.data.loader import Loader
from optim.settings import Settings
from optim.sources import Source, SourceName, mssql, oracle, rest


@dataclass(frozen=True)
class Context:
    loader: Loader
    oms: httpx.Client
    snapshot_dir: Path


@contextmanager
def open_context(settings: Settings, *, cache: bool = True) -> Generator[Context]:
    """Everything closes on exit. `cache=False` reads every dataset live."""
    with ExitStack() as stack:
        sources: dict[SourceName, Source] = {
            "oracle": stack.enter_context(oracle.connect(settings.oracle)),
            "mssql": mssql.connect(settings.mssql),
            "signals": rest.Rest(stack.enter_context(rest.client(settings.signals))),
        }
        oms = stack.enter_context(rest.client(settings.oms))
        yield Context(Loader(sources, _cache(settings, stack) if cache else None), oms, settings.snapshot_dir)


def _cache(settings: Settings, stack: ExitStack) -> Cache:
    if settings.redis_url is None:
        return MemoryCache()
    return RedisCache(stack.enter_context(redis.Redis.from_url(str(settings.redis_url), socket_timeout=5)))
