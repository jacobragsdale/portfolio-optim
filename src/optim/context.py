"""The process's connections, opened once and shared by every job: the CLI opens one per command. Every source, the cache
and the snapshot root are wired here and nowhere else."""

import threading
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime

from optim.data.cache import FileCache
from optim.data.loader import Loader
from optim.logs import utc_now
from optim.settings import Settings
from optim.sources import Source, SourceName, mssql, oracle, rest
from optim.sources.file import FileSource
from optim.storage import Root


@dataclass(frozen=True)
class Context:
    loader: Loader
    snapshot_root: Root
    workers: int  # solver processes per run; 1 solves in this process
    clock: Callable[[], datetime] = utc_now  # stamps the run's start, load and finish


@contextmanager
def open_context(settings: Settings, *, cache: bool = True) -> Generator[Context]:
    """Everything closes on exit. `cache=False`, or no cache root, reads every dataset live."""
    snapshot_root = Root.at(settings.snapshot_root)
    snapshot_root.make_dir()  # storage that cannot be written fails the run before it solves, not after
    with ExitStack() as stack:
        sources: dict[SourceName, Source] = {
            "oracle": oracle.connect(settings.oracle),
            "mssql": mssql.connect(settings.mssql),
            "signals": rest.RestSource(stack.enter_context(rest.client(settings.signals)), settings.signals.max_rows),
            "file": FileSource(),
        }
        limits: dict[SourceName, threading.Semaphore] = {name: threading.Semaphore(getattr(settings, name).max_concurrent) for name in ("oracle", "mssql", "signals")}
        yield Context(Loader(sources, open_cache(settings) if cache else None, limits), snapshot_root, settings.workers)


def open_cache(settings: Settings) -> FileCache | None:
    """The dataset cache, or None without `OPTIM_CACHE_ROOT`."""
    return FileCache(Root.at(settings.cache_root)) if settings.cache_root else None
