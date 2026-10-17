"""The process's connections, opened once and shared by every job: the CLI opens one per command, and an API server
would open one in its lifespan handler. Every source, cache and client is wired here and nowhere else."""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import redis
from pydantic import RedisDsn

from optim.cache import RedisCache
from optim.data.loader import Loader
from optim.settings import Settings
from optim.sources import Source, SourceName, mssql, oracle, rest


@dataclass(frozen=True)
class Context:
    loader: Loader
    oms: httpx.Client
    snapshot_dir: Path
    workers: int  # solver processes per run; 1 solves in this process


@contextmanager
def open_context(settings: Settings, *, cache: bool = True) -> Generator[Context]:
    """Everything closes on exit. `cache=False`, or no Redis, reads every dataset live."""
    with ExitStack() as stack:
        sources: dict[SourceName, Source] = {
            "oracle": stack.enter_context(oracle.connect(settings.oracle)),
            "mssql": mssql.connect(settings.mssql),
            "signals": rest.Rest(stack.enter_context(rest.client(settings.signals))),
        }
        oms = stack.enter_context(rest.client(settings.oms))
        redis_cache = stack.enter_context(open_cache(settings.redis_url)) if cache and settings.redis_url else None
        yield Context(Loader(sources, redis_cache), oms, settings.snapshot_dir, settings.workers)


@contextmanager
def open_cache(url: RedisDsn) -> Generator[RedisCache]:
    """The dataset cache alone, for `optim clear`, which needs no sources."""
    with redis.Redis.from_url(str(url), socket_timeout=5) as client:
        yield RedisCache(client)
